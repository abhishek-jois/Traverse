"""Cross-file resolution and weighted-graph construction.

Takes the per-file :class:`ExtractResult`s and resolves each raw import into an
actual file node, producing a NetworkX ``DiGraph`` whose edges carry a 1-10
dependency weight. Resolution is intentionally *internal-only*: imports of
third-party packages are dropped — we only graph files that exist in the repo.
"""

from __future__ import annotations

import sys

import networkx as nx

from .extractors import ExtractResult, Import
from .scanner import FileNode
from .weights import Relationship, compute_weight, confidence_for, relation_labels

_JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]
_STDLIB = set(getattr(sys, "stdlib_module_names", ()))


class _Index:
    """Lookup tables for turning import strings into node paths."""

    def __init__(self, nodes: list[FileNode]):
        self.paths = {n.path for n in nodes}
        self.by_noext: dict[str, str] = {}          # "src/models/user" -> path
        self.by_dotted: dict[str, str] = {}         # "models.user" -> path (py)
        self.by_basename: dict[str, list[str]] = {} # "user" -> [paths]
        for n in nodes:
            noext = _strip_ext(n.path)
            self.by_noext[noext] = n.path
            base = noext.rsplit("/", 1)[-1]
            self.by_basename.setdefault(base, []).append(n.path)
            # Python dotted suffixes — only .py files can be Python import
            # targets (otherwise `import json` matches a data file json.json).
            if n.language != "python":
                continue
            parts = noext.split("/")
            for i in range(len(parts)):
                suffix = ".".join(parts[i:])
                # last writer wins; longer (more specific) paths registered first
                self.by_dotted.setdefault(suffix, n.path)

    def exact_file(self, parts: list[str]) -> str | None:
        return self.by_noext.get("/".join(parts))


def _strip_ext(path: str) -> str:
    base, _, ext = path.rpartition(".")
    return base if base else path


def _resolve_all(nodes: list[FileNode], extracts: dict[str, ExtractResult],
                 index: "_Index") -> dict[tuple[str, str], tuple[Relationship, bool]]:
    """Resolve every file's imports into ``(src, dst) -> (Relationship, exact)``.

    Pure in-memory dict work (no parsing, no I/O) once ``extracts`` is in hand,
    so it is cheap to re-run for an incremental update.
    """
    node_by_path = {n.path: n for n in nodes}
    rels: dict[tuple[str, str], tuple[Relationship, bool]] = {}
    for node in nodes:
        res = extracts.get(node.path)
        if not res:
            continue
        for imp in res.imports:
            targets, exact = _resolve(node, imp, index)
            for tgt in targets:
                if tgt == node.path or tgt not in node_by_path:
                    continue
                key = (node.path, tgt)
                rel, prev_exact = rels.get(key, (Relationship(), exact))
                rel.import_count += 1
                rel.symbol_count += len(imp.symbols)
                rel.usage_total += sum(
                    res.usage_counts.get(ln, 0) for ln in imp.local_names
                )
                rel.has_inheritance |= bool(imp.base_classes)
                rel.is_reexport |= imp.is_reexport
                rel.target_is_config |= node_by_path[tgt].always_include
                rels[key] = (rel, prev_exact and exact)
    return rels


def _edge_attrs(rel: Relationship, exact: bool) -> dict:
    return {
        "weight": compute_weight(rel),
        "relations": relation_labels(rel),
        "confidence": confidence_for(rel, exact),
        "symbol_count": rel.symbol_count,
        "usage": rel.usage_total,
    }


def build_graph(nodes: list[FileNode],
                extracts: dict[str, ExtractResult]) -> nx.DiGraph:
    """Build the weighted dependency :class:`~networkx.DiGraph` from scratch."""
    index = _Index(nodes)
    rels = _resolve_all(nodes, extracts, index)

    g = nx.DiGraph()
    for n in nodes:
        g.add_node(n.path, **n.to_dict())
    for (src, dst), (rel, exact) in rels.items():
        attrs = _edge_attrs(rel, exact)
        # __init__.py files re-export everything — cap their edge weights so they
        # don't act as false hubs connecting unrelated modules.
        if (src.rsplit("/", 1)[-1] == "__init__.py"
                or dst.rsplit("/", 1)[-1] == "__init__.py"):
            attrs["weight"] = min(attrs["weight"], 1)
        g.add_edge(src, dst, **attrs)

    _annotate_degree(g)
    return g


def sync_graph(g: nx.DiGraph, nodes: list[FileNode],
               extracts: dict[str, ExtractResult], *,
               added: set[str], changed: set[str],
               deleted: set[str]) -> dict[str, int]:
    """Patch ``g`` in place so it matches the current ``nodes`` / ``extracts``.

    Only the changed part of the graph is touched: deleted files drop out
    (with their edges), added/changed files get fresh node metadata, and the
    edge set is re-resolved and applied as a *delta* (edges that did not change
    are left untouched). ``nodes``/``extracts`` must describe the full current
    file set so cross-file edges resolve correctly — e.g. a brand-new file can
    satisfy an import that an unchanged file already had.

    Returns a small report of how many nodes/edges were added or removed.
    """
    node_by_path = {n.path: n for n in nodes}

    # --- nodes -----------------------------------------------------------
    for p in deleted:
        if p in g:
            g.remove_node(p)            # networkx drops incident edges too
    for p in added | changed:
        n = node_by_path.get(p)
        if n is None:
            continue
        attrs = n.to_dict()
        if p in g:
            g.nodes[p].clear()
            g.nodes[p].update(attrs)
        else:
            g.add_node(p, **attrs)

    # --- edges (re-resolve all, apply only the difference) ---------------
    index = _Index(nodes)
    rels = _resolve_all(nodes, extracts, index)
    new_edges = {key: _edge_attrs(rel, exact) for key, (rel, exact) in rels.items()}
    for (src, dst), attrs in new_edges.items():
        if (src.rsplit("/", 1)[-1] == "__init__.py"
                or dst.rsplit("/", 1)[-1] == "__init__.py"):
            attrs["weight"] = min(attrs["weight"], 1)

    old_keys = set(g.edges())
    new_keys = set(new_edges)
    removed_edges = old_keys - new_keys
    for src, dst in removed_edges:
        g.remove_edge(src, dst)
    added_edges = 0
    for (src, dst), attrs in new_edges.items():
        if (src, dst) not in old_keys:
            added_edges += 1
        g.add_edge(src, dst, **attrs)   # overwrites attrs if the edge existed

    _annotate_degree(g)
    return {
        "nodes_added": len(added),
        "nodes_changed": len(changed),
        "nodes_removed": len(deleted),
        "edges_added": added_edges,
        "edges_removed": len(removed_edges),
    }


def _annotate_degree(g: nx.DiGraph) -> None:
    """Store total degree on each node (used to size the visualisation)."""
    for n in g.nodes:
        g.nodes[n]["degree"] = g.in_degree(n) + g.out_degree(n)


# --------------------------------------------------------------------------
# Import resolution
# --------------------------------------------------------------------------

def _resolve(node: FileNode, imp: Import, index: _Index) -> tuple[list[str], bool]:
    """Resolve one import to a list of internal node paths.

    Returns ``(paths, exact)`` where ``exact`` is False for fuzzy basename
    matches (which become INFERRED edges).
    """
    if node.language == "python":
        return _resolve_python(node, imp, index)
    return _resolve_js(node, imp, index)


def _resolve_python(node: FileNode, imp: Import, index: _Index) -> tuple[list[str], bool]:
    dir_parts = node.path.split("/")[:-1]

    if imp.is_relative or imp.level > 0:
        base = dir_parts[: len(dir_parts) - (imp.level - 1)] if imp.level > 0 else dir_parts
        mod_parts = imp.module.split(".") if imp.module else []
        if mod_parts:
            target = base + mod_parts
            hit = (index.exact_file(target)
                   or index.exact_file(target + ["__init__"]))
            if hit:
                return [hit], True
            # `from .pkg import submodule` -> each symbol may be a module file
            out = [p for sym in imp.symbols
                   if (p := index.exact_file(target + [sym]))]
            return (out, True) if out else ([], True)
        # `from . import x, y` -> resolve each symbol within the package dir
        out = []
        for sym in imp.symbols:
            hit = index.exact_file(base + [sym]) or index.exact_file(base + [sym, "__init__"])
            if hit:
                out.append(hit)
        return out, True

    # Stdlib modules never resolve to internal files (`import json`, `import os`).
    if imp.module.split(".")[0] in _STDLIB:
        return [], True

    # Absolute / package import: match dotted suffix against an internal file.
    dotted = index.by_dotted.get(imp.module)
    if dotted:
        return [dotted], True
    # `import pkg.sub` where pkg.sub is a package -> its __init__
    parts = imp.module.split(".")
    pkg_init = index.exact_file(parts + ["__init__"])
    if pkg_init:
        return [pkg_init], True
    # try resolving symbols as submodules of a matched package
    if imp.symbols:
        out = [p for sym in imp.symbols
               if (p := index.by_dotted.get(f"{imp.module}.{sym}"))]
        if out:
            return out, True
    return [], True


def _resolve_js(node: FileNode, imp: Import, index: _Index) -> tuple[list[str], bool]:
    mod = imp.module
    dir_parts = node.path.split("/")[:-1]

    if mod.startswith("."):
        clean = mod
        ups = 0
        while clean.startswith("../"):
            ups += 1
            clean = clean[3:]
        if clean.startswith("./"):
            clean = clean[2:]
        base = dir_parts[: len(dir_parts) - ups] if ups else dir_parts
        target = base + [p for p in clean.split("/") if p and p != "."]
        hit = _js_file_candidates(target, index)
        return ([hit], True) if hit else ([], True)

    # Bare specifier: usually a node_modules package. Only resolve if it maps
    # cleanly onto an internal path (alias / baseUrl style imports).
    if "/" in mod:
        target = [p for p in mod.split("/") if p]
        hit = _js_file_candidates(target, index)
        if hit:
            return [hit], False  # inferred
    return [], True


def _js_file_candidates(target: list[str], index: _Index) -> str | None:
    joined = "/".join(target)
    # already extension-qualified?
    if joined in index.paths:
        return joined
    if joined in index.by_noext:
        return index.by_noext[joined]
    for ext in _JS_EXTS:
        if (joined + ext) in index.paths:
            return joined + ext
    # directory index file
    for ext in _JS_EXTS:
        cand = f"{joined}/index{ext}"
        if cand in index.paths:
            return cand
    return None
