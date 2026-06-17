"""Incremental graph maintenance.

After the first full build, the graph is kept in sync with the filesystem
*cheaply*:

  1. a stat-only sweep (no file reads) finds which tracked files were added,
     changed (by size/mtime, confirmed by content hash) or deleted;
  2. only those files are re-parsed — everything else is reused from the
     extraction cache;
  3. the graph is patched **in place**: deleted files drop out with their
     edges, added/changed files get fresh nodes, and the edge set is applied as
     a delta so untouched edges are left alone.

This is what lets the graph track edits an agent makes — change a file and the
next query sees an up-to-date graph without ever rebuilding the whole thing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import networkx as nx

from . import describe, graph_builder, html_export, scanner, store
from .extractors import ExtractResult, extract


@dataclass
class SyncReport:
    root: str
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0
    rebuilt: bool = False              # had to build from scratch
    graph: nx.DiGraph | None = field(default=None, repr=False)

    @property
    def touched(self) -> bool:
        return self.rebuilt or bool(self.added or self.changed or self.deleted)

    def summary(self) -> str:
        if self.rebuilt:
            return "built graph from scratch"
        if not self.touched:
            return "graph already up to date"
        bits = []
        if self.added:
            bits.append(f"{len(self.added)} added")
        if self.changed:
            bits.append(f"{len(self.changed)} changed")
        if self.deleted:
            bits.append(f"{len(self.deleted)} removed")
        bits.append(f"edges +{self.edges_added}/-{self.edges_removed}")
        return ", ".join(bits)


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def _extract_for(root: str, node, *, force: bool) -> ExtractResult:
    cached = None if force else store.cache_get(root, node.sha256)
    if cached is not None:
        return cached
    res = extract(node)
    store.cache_put(root, node.sha256, res)
    return res


def _write_html(g: nx.DiGraph, root: str) -> str:
    html_path = os.path.join(store.out_dir(root), "graph.html")
    html_export.export_html(
        g, html_path, title=f"Dependency Graph · {os.path.basename(root)}")
    return html_path


def full_build(root: str, *, rebuild: bool = False, include_docs: bool = False,
               write_html: bool = True, errors: list[str] | None = None) -> nx.DiGraph:
    """Build the graph from scratch and persist it (+ the HTML viewer)."""
    root = os.path.abspath(root)
    nodes = scanner.scan(root, include_docs=include_docs, errors=errors)
    extracts: dict[str, ExtractResult] = {}
    for n in nodes:
        res = _extract_for(root, n, force=rebuild)
        n.symbols = res.defined_symbols[:12]
        extracts[n.path] = res
    describe.describe_all([(n, extracts[n.path]) for n in nodes])
    g = graph_builder.build_graph(nodes, extracts)
    store.save_graph(g, root,
                     meta={"languages": sorted({n.language for n in nodes})})
    if write_html:
        _write_html(g, root)
    return g


# --------------------------------------------------------------------------
# incremental sync
# --------------------------------------------------------------------------

def sync(root: str, *, include_docs: bool = False,
         write_html: bool = True) -> SyncReport:
    """Bring the persisted graph in line with the current filesystem, cheaply.

    Builds from scratch if no graph exists yet. Otherwise does the stat-sweep /
    re-parse-changed-only / patch-in-place dance described in the module
    docstring. The (possibly unchanged) graph is returned on ``report.graph``.
    """
    root = os.path.abspath(root)
    report = SyncReport(root=root)

    if not store.graph_exists(root):
        report.graph = full_build(root, include_docs=include_docs,
                                   write_html=write_html)
        report.rebuilt = True
        return report

    g, _ = store.load_graph(root)
    report.graph = g

    # Cheap stat-only sweep of the current tree.
    current = scanner.scan(root, include_docs=include_docs, stat_only=True)
    cur_by_path = {n.path: n for n in current}
    cur_paths = set(cur_by_path)
    old_paths = set(g.nodes)

    deleted = old_paths - cur_paths
    added = cur_paths - old_paths

    changed: set[str] = set()
    for p in cur_paths & old_paths:
        n = cur_by_path[p]
        od = g.nodes[p]
        if n.size != od.get("size") or abs(n.mtime - od.get("mtime", 0.0)) > 1e-6:
            # size/mtime differ — confirm with a content hash before real work
            scanner.fill_hash(n)
            if n.sha256 and n.sha256 != od.get("sha256"):
                changed.add(p)

    if not (added or changed or deleted):
        return report                      # already current — fast common case

    # Hash newly-added files; unchanged files inherit their stored hash so the
    # extraction cache hits without re-reading them.
    for p in added:
        scanner.fill_hash(cur_by_path[p])
    for p in cur_paths & old_paths:
        if p not in changed:
            cur_by_path[p].sha256 = g.nodes[p].get("sha256", "")

    dirty = added | changed
    extracts: dict[str, ExtractResult] = {}
    for n in current:
        res = _extract_for(root, n, force=(n.path in dirty))
        n.symbols = res.defined_symbols[:12]
        extracts[n.path] = res

    # Describe only the new/changed files; unchanged nodes keep their text.
    describe.describe_all([(cur_by_path[p], extracts[p]) for p in dirty])

    delta = graph_builder.sync_graph(
        g, current, extracts, added=added, changed=changed, deleted=deleted)
    store.save_graph(g, root,
                     meta={"languages": sorted({n.language for n in current})})
    if write_html:
        _write_html(g, root)

    report.added = sorted(added)
    report.changed = sorted(changed)
    report.deleted = sorted(deleted)
    report.edges_added = delta["edges_added"]
    report.edges_removed = delta["edges_removed"]
    return report
