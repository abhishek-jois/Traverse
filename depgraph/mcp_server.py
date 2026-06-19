"""MCP server — exposes Dependency Graph Retrieval to Claude Code as a tool.

Run as a stdio MCP server:

    python -m depgraph.mcp_server

Registered with Claude Code, this gives the agent one tool:

  * ``depgraph_query`` — given a natural-language task, return the minimal set
    of files that matter (with descriptions, why each was picked, and how many
    tokens it saves versus reading the whole repo). Claude then reads only those
    files instead of grepping blindly.

The graph builds automatically on first query and stays current via incremental
sync on every subsequent call. The graph is metadata-only, so calls are cheap.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import incremental
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
    """Return the minimal set of files relevant to a task, via the dependency graph.

    Args:
        query: natural-language task, e.g. "how does the training loop work".
        path: repo root (default: current directory).
        depth: traversal depth — "focused" / "balanced" (default) / "deep" / "exhaustive".
        max_files: hard file-count override (0 = use depth preset).
    """
    root = _resolve_root(path)
    if not os.path.isdir(root):
        return f"error: {root} is not a directory"
    if depth not in DEPTH_PRESETS:
        depth = DEFAULT_DEPTH
    g = _ensure_graph(root)

    if g.number_of_nodes() < 80:
        return (
            f"Repo has {g.number_of_nodes()} files — small enough to navigate directly. "
            "Use Grep or LS instead; it will be faster and cheaper than the graph for this repo size."
        )

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

    # Inline file contents when the total is small enough — eliminates N separate
    # Read calls, collapsing N turns of accumulated context into turn 1.
    _INLINE_TOKEN_CAP = 6000
    if result.selected_tokens <= _INLINE_TOKEN_CAP:
        lines.append("File contents are included below — no separate Read calls needed.")
        lines.append("")
        lines.append("=== FILE CONTENTS ===")
        for s in result.selected:
            abs_path = os.path.join(root, s.path)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                lines.append(f"\n--- {abs_path} ---")
                lines.append(content)
            except OSError:
                pass
    else:
        lines.append("Read the files above; the rest of the repo is unlikely to be "
                     "relevant to this task.")
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
