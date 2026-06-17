"""MCP server — exposes Dependency Graph Retrieval to Claude Code as tools.

Run as a stdio MCP server:

    python -m depgraph.mcp_server

Registered with Claude Code, this gives the agent three tools:

  * ``depgraph_query`` — the core: given a natural-language task, return the
    handful of files that matter (with descriptions, why each was picked, and
    how many tokens it saves versus reading the whole repo). Claude then reads
    only those files instead of grepping blindly.
  * ``depgraph_map``  — a cheap repo overview (size, file-type mix, the most
    connected "god" files) for orientation.
  * ``depgraph_build`` — (re)build the graph for a repo; queries auto-build on
    first use, so this is only needed to force a refresh.

The graph itself is metadata-only, so these calls stay cheap.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import incremental, store
from .retrieve import DEFAULT_DEPTH, DEPTH_PRESETS, retrieve

mcp = FastMCP("depgraph")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _resolve_root(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path or "."))


def _ensure_graph(root: str):
    """Return an up-to-date graph: build on first use, else incrementally sync.

    The sync is cheap when nothing changed (a stat-only sweep), so calling this
    before every query keeps the graph current with whatever the agent just
    edited, without rebuilding.
    """
    return incremental.sync(root).graph


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

@mcp.tool()
def depgraph_query(query: str, path: str = ".",
                   depth: str = DEFAULT_DEPTH, max_files: int = 0) -> str:
    """Find the files most relevant to a task, via the dependency graph.

    Use this BEFORE grepping or reading files: it returns the minimal set of
    files you should load to work on ``query``, chosen by traversing a weighted
    dependency graph of the repo (not just text matching). Each result lists the
    absolute path to read, its role, a one-line description, why it was selected,
    and the token cost — plus the % saved versus reading the whole repo.

    Args:
        query: the task or question in natural language, e.g.
            "where is JWT auth handled" or "how does the training loop work".
        path: repo root to search (default: current directory). The graph is
            built automatically on first use and cached.
        depth: how far to traverse / how many files to return — one of
            "focused" (~5 files, tightest), "balanced" (default, ~8),
            "deep" (~12), "exhaustive" (~20). Increase when the first result
            misses files or the task spans many modules.
        max_files: hard override for the number of files (0 = use the depth
            preset).
    """
    root = _resolve_root(path)
    if not os.path.isdir(root):
        return f"error: {root} is not a directory"
    if depth not in DEPTH_PRESETS:
        depth = DEFAULT_DEPTH
    g = _ensure_graph(root)

    result = retrieve(g, query, depth=depth,
                      max_files=(max_files or None))
    if not result.selected:
        return (f"No relevant files found for: {query!r}\n"
                f"(searched {result.total_files} files; try different terms "
                f"or a deeper depth)")

    lines = [f"Relevant files for: {query!r}  (depth={depth})", ""]
    for s in result.selected:
        lines.append(os.path.join(root, s.path))
        lines.append(f"    [{s.file_type}] {s.description}")
        lines.append(f"    why: {s.reason}  · ~{s.tokens} tok")
        lines.append("")
    lines.append(
        f"Selected {len(result.selected)} of {result.total_files} files · "
        f"{result.selected_tokens:,} tok vs {result.total_tokens:,} full repo "
        f"→ {result.savings_pct:.1f}% saved.")
    lines.append("Read the files above; the rest of the repo is unlikely to be "
                 "relevant to this task.")
    return "\n".join(lines)


@mcp.tool()
def depgraph_map(path: str = ".", top: int = 12) -> str:
    """Get a cheap structural overview of a repo from its dependency graph.

    Returns the file count, the file-type mix, and the most connected files
    (the architectural hubs / "god nodes"). Good for orienting yourself in an
    unfamiliar codebase before diving in. Builds the graph if needed.

    Args:
        path: repo root (default: current directory).
        top: how many hub files to list (default 12).
    """
    root = _resolve_root(path)
    if not os.path.isdir(root):
        return f"error: {root} is not a directory"
    g = _ensure_graph(root)

    types: dict[str, int] = {}
    for n in g.nodes:
        t = g.nodes[n].get("file_type", "other")
        types[t] = types.get(t, 0) + 1
    type_line = ", ".join(f"{t}={c}" for t, c in
                          sorted(types.items(), key=lambda kv: -kv[1]))

    hubs = sorted(g.nodes, key=lambda n: g.nodes[n].get("degree", 0),
                  reverse=True)[:top]

    lines = [f"Repository map · {root}",
             f"{g.number_of_nodes()} files, {g.number_of_edges()} dependencies",
             f"Types: {type_line}", "",
             "Most connected files (start here to understand the architecture):"]
    for n in hubs:
        d = g.nodes[n]
        lines.append(f"  {d.get('degree', 0):>3} links  {n}")
        if d.get("description"):
            lines.append(f"            {d['description']}")
    return "\n".join(lines)


@mcp.tool()
def depgraph_build(path: str = ".", rebuild: bool = False) -> str:
    """Build or refresh the dependency graph for a repo.

    Queries build the graph automatically, so call this only to force a refresh
    after the code changed a lot. Writes ``.depgraph/graph.json`` and a
    self-contained ``.depgraph/graph.html`` viewer.

    Args:
        path: repo root (default: current directory).
        rebuild: ignore the extraction cache and re-parse every file.
    """
    root = _resolve_root(path)
    if not os.path.isdir(root):
        return f"error: {root} is not a directory"
    g = incremental.full_build(root, rebuild=rebuild)
    out = store.out_dir(root)
    return (f"Built dependency graph for {root}: "
            f"{g.number_of_nodes()} files, {g.number_of_edges()} dependencies.\n"
            f"Viewer: {os.path.join(out, 'graph.html')}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
