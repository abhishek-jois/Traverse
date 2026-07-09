"""Cross-file resolution and weighted-graph construction.

Takes the per-file :class:`ExtractResult`s and resolves each raw import into an
actual file node, producing a NetworkX ``DiGraph`` whose edges carry a 1-10
dependency weight. Resolution is intentionally *internal-only*: imports of
third-party packages are dropped — we only graph files that exist in the repo.
"""

from __future__ import annotations

import posixpath
import re as _re
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
        # Slash-path suffixes for languages whose imports mirror the directory
        # layout (Java packages, Rust crate paths): "com/x/Foo" -> [paths].
        self.path_suffix: dict[str, list[str]] = {}
        # Directory-path suffixes + the files in each dir, for Go package imports.
        self.dir_suffix: dict[str, str] = {}        # "internal/auth" -> "a/b/internal/auth"
        self.dir_files: dict[str, list[str]] = {}   # dir -> [paths of code files in it]
        for n in nodes:
            noext = _strip_ext(n.path)
            self.by_noext[noext] = n.path
            base = noext.rsplit("/", 1)[-1]
            self.by_basename.setdefault(base, []).append(n.path)

            d = n.path.rsplit("/", 1)[0] if "/" in n.path else ""
            self.dir_files.setdefault(d, []).append(n.path)

            if n.language in ("java", "rust", "c", "cpp"):
                parts = noext.split("/")
                for i in range(len(parts)):
                    self.path_suffix.setdefault("/".join(parts[i:]), []).append(n.path)
            elif n.language == "go":
                dparts = d.split("/") if d else []
                for i in range(len(dparts)):
                    # last writer wins; favours the first (shallowest) registrant
                    self.dir_suffix.setdefault("/".join(dparts[i:]), d)

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

    _add_http_edges(g)
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

    _add_http_edges(g)
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
# Cross-language HTTP edge detection
# --------------------------------------------------------------------------

_PARAM_NORM_RE = _re.compile(r"/\{[^}]+\}|/:[a-zA-Z_]\w*|/\d+(?=/|$)")


def _norm_route(path: str) -> str:
    """Normalise path parameters to * for route matching."""
    return _PARAM_NORM_RE.sub("/*", path).rstrip("/") or "/"


def _routes_match(server: str, client: str) -> bool:
    """Return True if the client call path maps onto the server route."""
    s_parts = server.split("/")
    c_parts = client.split("/")
    if len(s_parts) < 2:   # skip bare "/" — too ambiguous
        return False
    if len(c_parts) < len(s_parts):
        return False
    return all(sp == "*" or sp == cp for sp, cp in zip(s_parts, c_parts))


def _add_http_edges(g: nx.DiGraph) -> None:
    """Add weight-4 cross-language edges where HTTP calls match route decorators.

    Scans Python files for exposed routes (FastAPI/Flask decorators) and
    JS/TS files for outgoing fetch/axios calls, then links matching pairs.
    This bridges the Python↔TypeScript boundary that import-based edges cannot.
    """
    server_index: list[tuple[str, str]] = []   # (normalised_route, node_path)
    for n in g.nodes:
        for route in g.nodes[n].get("http_routes", []):
            server_index.append((_norm_route(route), n))

    if not server_index:
        return

    for n in g.nodes:
        for call in g.nodes[n].get("http_calls", []):
            call_norm = _norm_route(call)
            for srv_norm, srv_node in server_index:
                if srv_node == n:
                    continue
                if _routes_match(srv_norm, call_norm):
                    if g.has_edge(n, srv_node):
                        g[n][srv_node]["weight"] = min(10, g[n][srv_node]["weight"] + 1)
                        rels = g[n][srv_node].get("relations", [])
                        if "http" not in rels:
                            g[n][srv_node]["relations"] = rels + ["http"]
                    else:
                        g.add_edge(n, srv_node, weight=4, relations=["http"],
                                   confidence="inferred", symbol_count=0, usage=1)


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
    if node.language == "go":
        return _resolve_go(node, imp, index)
    if node.language == "rust":
        return _resolve_rust(node, imp, index)
    if node.language == "java":
        return _resolve_java(node, imp, index)
    if node.language in ("c", "cpp"):
        return _resolve_c(node, imp, index)
    return _resolve_js(node, imp, index)


def _resolve_java(node: FileNode, imp: Import, index: _Index) -> tuple[list[str], bool]:
    """Java imports mirror the package directory: ``a.b.C`` -> ``…/a/b/C.java``."""
    mod = imp.module
    if mod.endswith(".*"):                       # wildcard: link the whole package dir
        pkg = mod[:-2].replace(".", "/")
        d = index.dir_suffix.get(pkg)
        files = index.dir_files.get(d, []) if d is not None else []
        return ([p for p in files if p != node.path], True)
    path = mod.replace(".", "/")
    hits = index.path_suffix.get(path)
    return ([hits[0]], True) if hits else ([], True)


def _resolve_go(node: FileNode, imp: Import, index: _Index) -> tuple[list[str], bool]:
    """Go imports name a package directory; link to the .go files in it.

    The module prefix (from go.mod) is unknown, so we match the import-path
    suffix against an internal directory, longest suffix first. Stdlib paths
    (``fmt``, ``net/http``) match nothing internal and drop out.
    """
    parts = imp.module.split("/")
    for i in range(len(parts)):
        suffix = "/".join(parts[i:])
        # Require ≥2 segments (or a 1-segment import) to avoid matching a common
        # leaf dir name like "util" by accident.
        if len(suffix.split("/")) < 2 and len(parts) > 1:
            continue
        d = index.dir_suffix.get(suffix)
        if d is not None:
            files = [p for p in index.dir_files.get(d, [])
                     if p != node.path and p.endswith(".go")]
            if files:
                return (files[:12], i == 0)      # exact only when the full path matched
    return [], True


def _resolve_c(node: FileNode, imp: Import, index: _Index) -> tuple[list[str], bool]:
    """Resolve a C/C++ ``#include "path"`` to an internal header/source file.

    A quoted include names a path relative to the including file or a project
    include root; the compiler's ``-I`` search paths are unknown to us, so we try,
    in order: (1) relative to the including file's own directory (handling
    ``./`` and ``../``); (2) a path-suffix match anywhere in the tree; (3) a bare
    basename match (an INFERRED edge). Angle-bracket ``<...>`` includes are system
    headers and never reach here (the extractor keeps only quoted includes).
    """
    noext = _strip_ext(imp.module).replace("\\", "/")

    # 1. Relative to the including file's directory (normalises ./ and ../).
    d = node.path.rsplit("/", 1)[0] if "/" in node.path else ""
    rel = posixpath.normpath(f"{d}/{noext}" if d else noext)
    hit = index.by_noext.get(rel)
    if hit and hit != node.path:
        return [hit], True

    # 2. Path-suffix match anywhere in the tree (project include root unknown).
    hits = index.path_suffix.get(noext.lstrip("./"))
    if hits:
        picked = [p for p in hits if p != node.path]
        if picked:
            return [picked[0]], True

    # 3. Bare basename fallback -> inferred edge.
    base = noext.rsplit("/", 1)[-1]
    cand = [p for p in index.by_basename.get(base, []) if p != node.path]
    if cand:
        return [cand[0]], False
    return [], True


def _resolve_rust(node: FileNode, imp: Import, index: _Index) -> tuple[list[str], bool]:
    """Resolve ``mod foo;`` (sibling file) and ``use crate::a::b`` (path match)."""
    d = node.path.rsplit("/", 1)[0] if "/" in node.path else ""
    if "::" not in imp.module:                   # `mod foo;`
        name = imp.module
        for cand in (f"{d}/{name}" if d else name, f"{d}/{name}/mod" if d else f"{name}/mod"):
            hit = index.by_noext.get(cand)
            if hit and hit != node.path:
                return [hit], True
        return [], True
    # `use crate::a::b::C` — match the path, then the path without its last
    # segment (which is usually an item, not a file).
    rest = [p for p in imp.module.split("::")[1:] if p not in ("super", "self")]
    for cand in ("/".join(rest), "/".join(rest[:-1])):
        if not cand:
            continue
        hits = index.path_suffix.get(cand)
        if hits:
            picked = [p for p in hits if p != node.path]
            if picked:
                return [picked[0]], False
    return [], True


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
