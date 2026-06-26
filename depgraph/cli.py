"""Command-line interface for Dependency Graph Retrieval.

    depgraph build [path] [--llm] [--rebuild]   build the graph + graph.html
    depgraph query "..." [--max N] [--open]      traverse to the relevant files
    depgraph viz [path] [--open]                 regenerate / open graph.html
    depgraph stats [path]                        print graph statistics
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

from . import (describe, graph_builder, html_export, incremental, llm,
               scanner, store)
from .extractors import extract
from .retrieve import retrieve


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def cmd_build(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    print(f"Scanning {root} …")
    scan_errors: list[str] = []
    nodes = scanner.scan(root, include_docs=args.docs, errors=scan_errors)
    print(f"  found {len(nodes)} files")
    for err in scan_errors:
        print(f"  ⚠ skipped (unreadable): {err}")

    print("Extracting dependencies …")
    extracts = {}
    reused = 0
    for node in nodes:
        cached = None if args.rebuild else store.cache_get(root, node.sha256)
        if cached is not None:
            res = cached
            reused += 1
        else:
            res = extract(node)
            store.cache_put(root, node.sha256, res)
        node.symbols = res.defined_symbols[:12]
        node.http_routes = res.http_routes
        node.http_calls = res.http_calls
        extracts[node.path] = res
    print(f"  parsed {len(nodes) - reused}, reused {reused} from cache")

    describe.describe_all([(n, extracts[n.path]) for n in nodes])
    if args.llm:
        if llm.is_available():
            print("Generating AI descriptions …")
            n_upd = llm.annotate(root, nodes)
            print(f"  updated {n_upd} descriptions via LLM")
        else:
            print("  --llm requested but ANTHROPIC_API_KEY / anthropic package "
                  "missing; using heuristic descriptions")

    print("Building weighted graph …")
    g = graph_builder.build_graph(nodes, extracts)
    print(f"  {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")

    store.save_graph(g, root, meta={"languages": sorted({n.language for n in nodes})})
    incremental._write_repo_map(g, root)
    html_path = os.path.join(store.out_dir(root), "graph.html")
    html_export.export_html(g, html_path, title=f"Dependency Graph · {os.path.basename(root)}")
    _write_report(g, root)

    print(f"\n✓ graph.json  → {os.path.join(store.OUT_DIRNAME, 'graph.json')}")
    print(f"✓ graph.html  → {os.path.join(store.OUT_DIRNAME, 'graph.html')}  (open in a browser)")
    print(f"✓ repo_map.md → {os.path.join(store.OUT_DIRNAME, 'repo_map.md')}")
    print(f"✓ report.md   → {os.path.join(store.OUT_DIRNAME, 'report.md')}")
    if args.open:
        webbrowser.open(f"file://{html_path}")
    return 0


# --------------------------------------------------------------------------
# query
# --------------------------------------------------------------------------

def cmd_query(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.path)
    if not store.graph_exists(root):
        print("error: no graph found. Run `depgraph build` first.", file=sys.stderr)
        return 2
    # Keep the graph current with any edits since the last build, cheaply.
    if not args.no_sync:
        rep = incremental.sync(root)
        if rep.touched:
            print(f"(synced: {rep.summary()})")
    g, _ = store.load_graph(root)
    result = retrieve(g, args.query, max_files=args.max,
                      cutoff=args.cutoff, depth=args.depth)

    print(f"\nQuery: {result.query}")
    print("─" * 60)
    if not result.selected:
        print("No relevant files found. Try different terms.")
        return 0
    for s in result.selected:
        print(f"  {s.path}")
        print(f"      [{s.file_type}] {s.description}")
        print(f"      why: {s.reason}  · ~{s.tokens} tok")
    print("─" * 60)
    print(f"Selected {len(result.selected)} of {result.total_files} files "
          f"(traversal cutoff {result.cutoff:.2f})")
    print(f"Tokens: {result.selected_tokens:,} loaded vs {result.total_tokens:,} full repo "
          f"→ {result.savings_pct:.1f}% saved")
    print(f"(graph-entry metadata scan cost ≈ {result.metadata_tokens:,} tok)")

    if args.pack:
        _print_pack(g, root, result)
    if args.open:
        html_path = os.path.join(store.out_dir(root), "graph.html")
        html_export.export_html(
            g, html_path, title=f"Query · {args.query}",
            highlight={s.path for s in result.selected}, query=args.query)
        webbrowser.open(f"file://{html_path}")
    return 0


def _print_pack(g, root, result) -> None:
    """Emit a compact context pack: all node metadata + selected file contents."""
    print("\n===== CONTEXT PACK (metadata for every file) =====")
    for n in sorted(g.nodes):
        d = g.nodes[n]
        print(f"- {n} [{d.get('file_type')}]: {d.get('description')}")
    print("\n===== SELECTED FILE CONTENTS =====")
    for s in result.selected:
        abspath = os.path.join(root, s.path)
        try:
            with open(abspath, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            body = "<unreadable>"
        print(f"\n----- {s.path} -----\n{body}")


# --------------------------------------------------------------------------
# viz / stats
# --------------------------------------------------------------------------

def cmd_update(args: argparse.Namespace) -> int:
    """Incrementally sync the graph with the filesystem (no full rebuild)."""
    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    rep = incremental.sync(root)
    if rep.rebuilt:
        print(f"No existing graph — {rep.summary()} "
              f"({rep.graph.number_of_nodes()} nodes, "
              f"{rep.graph.number_of_edges()} edges).")
        return 0
    print(f"Sync: {rep.summary()}")
    for p in rep.added:
        print(f"  + {p}")
    for p in rep.changed:
        print(f"  ~ {p}")
    for p in rep.deleted:
        print(f"  - {p}")
    if rep.touched:
        print(f"Graph now {rep.graph.number_of_nodes()} nodes, "
              f"{rep.graph.number_of_edges()} edges.")
    return 0


def cmd_viz(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.path)
    if not store.graph_exists(root):
        print("error: no graph found. Run `depgraph build` first.", file=sys.stderr)
        return 2
    g, _ = store.load_graph(root)
    html_path = os.path.join(store.out_dir(root), "graph.html")
    html_export.export_html(g, html_path, title=f"Dependency Graph · {os.path.basename(root)}")
    print(f"✓ {html_path}")
    if args.open:
        webbrowser.open(f"file://{html_path}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.path)
    if not store.graph_exists(root):
        print("error: no graph found. Run `depgraph build` first.", file=sys.stderr)
        return 2
    g, meta = store.load_graph(root)
    print(f"Nodes: {g.number_of_nodes()}   Edges: {g.number_of_edges()}")

    types: dict[str, int] = {}
    for n in g.nodes:
        t = g.nodes[n].get("file_type", "other")
        types[t] = types.get(t, 0) + 1
    print("\nFile types:")
    for t, c in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<12} {c}")

    print("\nMost connected files (god nodes):")
    god = sorted(g.nodes, key=lambda n: g.nodes[n].get("degree", 0), reverse=True)[:8]
    for n in god:
        print(f"  {g.nodes[n].get('degree', 0):>3} links  {n}")

    conf: dict[str, int] = {}
    for _, _, e in g.edges(data=True):
        conf[e.get("confidence", "?")] = conf.get(e.get("confidence", "?"), 0) + 1
    print("\nEdge confidence:", ", ".join(f"{k}={v}" for k, v in conf.items()) or "none")
    return 0


def _write_report(g, root: str) -> None:
    lines = ["# Dependency Graph Report", ""]
    lines.append(f"- Files (nodes): **{g.number_of_nodes()}**")
    lines.append(f"- Dependencies (edges): **{g.number_of_edges()}**")
    god = sorted(g.nodes, key=lambda n: g.nodes[n].get("degree", 0), reverse=True)[:10]
    lines += ["", "## Most connected files", ""]
    for n in god:
        lines.append(f"- `{n}` — {g.nodes[n].get('degree', 0)} links — "
                     f"{g.nodes[n].get('description', '')}")
    always = [n for n in g.nodes if g.nodes[n].get("always_include")]
    if always:
        lines += ["", "## Cross-cutting (always-include) files", ""]
        lines += [f"- `{n}`" for n in sorted(always)]
    with open(os.path.join(store.out_dir(root), "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# docker
# --------------------------------------------------------------------------

def cmd_docker(args: argparse.Namespace) -> int:
    import shutil
    import subprocess
    import tempfile

    if shutil.which("docker") is None:
        print("error: docker not found in PATH", file=sys.stderr)
        return 2

    container = args.container
    src_path = args.src_path.rstrip("/") or "/app"

    # Verify container is running before copying anything out.
    check = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        capture_output=True, text=True,
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        print(f"error: container '{container}' is not running or does not exist",
              file=sys.stderr)
        return 2

    tmp = tempfile.mkdtemp(prefix="depgraph_docker_")
    print(f"Extracting {container}:{src_path} → {tmp} …")
    r = subprocess.run(
        ["docker", "cp", f"{container}:{src_path}", tmp],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"error: docker cp failed:\n{r.stderr.strip()}", file=sys.stderr)
        return 2

    # docker cp puts the directory itself (basename) into tmp.
    extracted = os.path.join(tmp, os.path.basename(src_path))
    if not os.path.isdir(extracted):
        extracted = tmp  # fallback: cp may have flattened the layout

    print(f"Building graph for {container}:{src_path} …")
    g = incremental.full_build(extracted, rebuild=args.rebuild, write_html=True)
    print(f"  {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
    print(f"\n✓ Graph stored at: {os.path.join(extracted, store.OUT_DIRNAME)}")
    print(f"  Query it with:  depgraph query \"...\" {extracted}")
    return 0


# --------------------------------------------------------------------------
# arg parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="depgraph",
        description="Dependency Graph Retrieval — smarter context for smarter code.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="scan a repo and build the graph + graph.html")
    b.add_argument("path", nargs="?", default=".")
    b.add_argument("--llm", action="store_true", help="use Anthropic API for descriptions")
    b.add_argument("--rebuild", action="store_true", help="ignore the extraction cache")
    b.add_argument("--docs", action="store_true", help="include doc files as nodes")
    b.add_argument("--open", action="store_true", help="open graph.html when done")
    b.set_defaults(func=cmd_build)

    q = sub.add_parser("query", help="traverse the graph to find relevant files")
    q.add_argument("query")
    q.add_argument("path", nargs="?", default=".")
    q.add_argument("--max", type=int, default=None,
                   help="max files to select (default: set by --depth)")
    q.add_argument("--depth", choices=["focused", "balanced", "deep", "exhaustive"],
                   default="balanced",
                   help="how far the traversal reaches / how many files come back "
                        "(default: balanced)")
    q.add_argument("--cutoff", type=float, default=None,
                   help="traversal relevance cutoff 0-1 (default: adapts to repo size; "
                        "higher = shallower, more focused)")
    q.add_argument("--pack", action="store_true", help="print a ready-to-feed context pack")
    q.add_argument("--open", action="store_true", help="open graph.html with matches highlighted")
    q.add_argument("--no-sync", action="store_true",
                   help="skip the incremental filesystem sync before querying")
    q.set_defaults(func=cmd_query)

    u = sub.add_parser("update", help="incrementally sync the graph with file changes")
    u.add_argument("path", nargs="?", default=".")
    u.set_defaults(func=cmd_update)

    v = sub.add_parser("viz", help="regenerate / open the HTML viewer")
    v.add_argument("path", nargs="?", default=".")
    v.add_argument("--open", action="store_true")
    v.set_defaults(func=cmd_viz)

    s = sub.add_parser("stats", help="print graph statistics")
    s.add_argument("path", nargs="?", default=".")
    s.set_defaults(func=cmd_stats)

    d = sub.add_parser("docker", help="build graph from code inside a running Docker container")
    d.add_argument("container", help="container name or ID")
    d.add_argument("src_path", nargs="?", default="/app",
                   help="path inside the container to scan (default: /app)")
    d.add_argument("--rebuild", action="store_true", help="ignore the extraction cache")
    d.set_defaults(func=cmd_docker)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
