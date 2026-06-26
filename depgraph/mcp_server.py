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

from . import incremental, slicer
from .retrieve import DEFAULT_DEPTH, DEPTH_PRESETS, retrieve

mcp = FastMCP("depgraph")

# How many tokens of actual code to inline per query, by depth. Because slices are
# small (a few symbol bodies, not whole files) this stays cheap even when the
# response re-processes every turn — and it removes the per-file Read turns that
# the A/B runs identified as the real cost driver.
_CODE_BUDGET = {"focused": 1500, "balanced": 2500, "deep": 4000, "exhaustive": 6000}

# Below this node count we answer in one shot with a tight focused answer pack
# rather than telling the agent to go grep — the inlined slices cost one turn,
# the grep route cost several.
_SMALL_REPO_NODES = 80


def _build_answer_pack(root: str, query: str, selected, budget: int) -> list[str]:
    """Slice the selected files into an inline code block within ``budget`` tokens.

    Files are sliced in selection order (most relevant first); each consumes from
    the shared budget until it is spent. Returns the lines of an ``=== CODE ===``
    section, or an empty list if nothing could be inlined (caller's path list
    still stands).
    """
    # Cap any single file's share so a broad query still gets a taste of several
    # files rather than one big file eating the whole budget.
    per_file_cap = max(800, budget // 2)
    remaining = budget
    blocks: list[str] = []
    for s in selected:
        if remaining <= 200:
            break
        abs_path = os.path.join(root, s.path)
        sliced = slicer.slice_file(abs_path, query, min(remaining, per_file_cap))
        if sliced is None:
            continue
        tag = "whole file" if sliced.mode == "whole" else "sliced to query"
        blocks.append(f"\n--- {abs_path} ({tag}) ---\n{sliced.text}")
        remaining -= sliced.tokens
    if not blocks:
        return []
    return (["\n=== CODE (read a listed file in full only if a slice looks "
             "incomplete) ==="] + blocks)


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
    """Return the minimal set of files relevant to a codebase task via the dependency graph.

    WHEN TO CALL:
    - "how does X work", "explain Y", "fix/change/implement Z", "trace the flow of W"
    - Any question that spans multiple files or modules in THIS repo.
    - Call ONCE per question — trust the result, do not re-query with rephrased terms.

    WHEN TO SKIP — do NOT call this tool:
    - Pinpoint lookups ("where is X", "which file has Y", "find Z") → use Grep instead.
    - General knowledge ("what is OAuth", "explain recursion", "best practices for X")
      that has nothing to do with this specific codebase → answer directly.
    - After already calling it for the same question → re-read returned files instead.

    AFTER CALLING:
    - The response inlines a "=== CODE ===" section with the query-relevant slices
      already extracted (an outline of each file plus the relevant symbol bodies).
      Use it directly — do NOT re-Read a file whose needed code is already inlined.
    - Only Read a listed file in full if a slice is marked truncated or its outline
      shows a symbol you need that was not inlined (the outline gives line ranges).
    - Do not call depgraph_query again mid-task for the same question.

    DEPTH — only override if the default result is clearly wrong:
    - focused:    2–5 files.  Single-module, pinpoint questions.
    - balanced:   default.    Most tasks, auto-sized to query complexity.
    - deep:       8–12 files. Cross-module tasks, traces, unfamiliar areas.
    - exhaustive: up to 15.   Broad refactors or "everything touching X".

    REPO ORIENTATION (first call in a session):
    If .depgraph/repo_map.md exists at the repo root, read it once before calling
    this tool — it gives you the repo size tier and key hub files so you can decide
    whether to call the graph at all and at what depth.

    Args:
        query: natural-language description of the task or question.
        path:  repo root directory (default: current working directory).
        depth: traversal depth preset — focused/balanced/deep/exhaustive (default: balanced).
        max_files: hard file-count cap, 0 = use depth preset.
    """
    root = _resolve_root(path)
    if not os.path.isdir(root):
        return f"error: {root} is not a directory"
    if depth not in DEPTH_PRESETS:
        depth = DEFAULT_DEPTH
    g = _ensure_graph(root)

    # On a small repo we answer in one shot: focused depth + a tight code budget,
    # so the agent gets the relevant slices inline instead of grepping around.
    node_count = g.number_of_nodes()
    is_small = node_count < _SMALL_REPO_NODES
    if is_small:
        depth = "focused"

    # Safety-net: if the query looks like a pure knowledge question and the graph
    # finds nothing, redirect rather than returning weak file matches.
    _KNOWLEDGE_STARTERS = (
        "what is ", "what are ", "explain ", "difference between ",
        "how do i learn", "best practices", "when should i use", "why does ",
        "define ", "what does ",
    )
    _REPO_SIGNALS = ("this ", "our ", "here ", "codebase", "repo", ".py", ".ts", ".js", "_")
    q_lower = query.lower()
    _looks_like_research = (
        any(q_lower.startswith(s) or f" {s}" in q_lower for s in _KNOWLEDGE_STARTERS)
        and not any(sig in q_lower for sig in _REPO_SIGNALS)
    )

    result = retrieve(g, query, depth=depth,
                      max_files=(max_files or None))
    if not result.selected:
        if _looks_like_research:
            return (
                "This looks like a general knowledge question rather than a codebase question. "
                "Answer directly from your training — no files needed."
            )
        return (f"No relevant files found for: {query!r}\n"
                f"(searched {result.total_files} files; try different terms "
                f"or a deeper depth)")

    # Header: paths + 5-word hint. The response re-processes every turn as
    # cache-read, so keep it tight; the code below carries the actual content.
    lines = [f"Files for: {query!r}  ({len(result.selected)} found)"]
    for s in result.selected:
        abs_path = os.path.join(root, s.path)
        hint = " ".join(s.description.split()[:5]).rstrip(".,;:")
        lines.append(f"{abs_path}  # {hint}")
    lines.append(f"Read these files. (~{result.selected_tokens:,} tok of {result.total_tokens:,})")

    # Inline query-scoped code slices (not whole files). Slices are small enough
    # to stay cheap at any repo size, and they remove the per-file Read turns that
    # otherwise dominate cost — see depgraph/slicer.py.
    budget = _CODE_BUDGET.get(depth, _CODE_BUDGET[DEFAULT_DEPTH])
    lines += _build_answer_pack(root, query, result.selected, budget)
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
