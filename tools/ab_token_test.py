#!/usr/bin/env python3
"""A/B token harness: measure what Claude Code *really* spends on a task,
with the depgraph MCP graph vs. without it.

For each prompt it runs `claude -p` twice on the same repo:

  * WITHOUT — no MCP servers (strict empty config); Claude navigates with its
    normal grep/glob/read workflow.
  * WITH    — only the depgraph MCP server, plus a nudge to use it first.

Both runs are headless, read-only-ish, bounded by --max-turns, and report their
real token usage + cost via `--output-format json`. We diff those.

Usage:
    python tools/ab_token_test.py <repo> "<prompt>" ["<prompt2>" ...] \
        [--max-turns N] [--timeout SECONDS]

Note: this spends real API tokens (two full agent runs per prompt). Model
nondeterminism means single runs are noisy — read the trend across prompts,
not one number.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

VENV_PY = "/data/abhishek/graph/.venv/bin/python"

DEP_CONFIG = {
    "mcpServers": {
        "depgraph": {
            "type": "stdio",
            "command": VENV_PY,
            "args": ["-m", "depgraph.mcp_server"],
        }
    }
}
EMPTY_CONFIG = {"mcpServers": {}}

NUDGE = (
    "A dependency-graph MCP tool is available: depgraph_query(query, path). "
    "Before reading or grepping anything, call depgraph_query ONCE with the "
    "user's task to get the relevant files, then read ONLY those files. "
    "Do not re-query for the same task and do not open files not returned by the tool."
)


def _run(repo: str, prompt: str, cfg_path: str, *, with_graph: bool,
         max_turns: int, timeout: int) -> dict:
    # Read-only allowlist only — no bypass of approval gates, no edit/write/bash.
    # The nested agent can navigate (read/grep/glob) and, with the graph, query
    # it; anything else is denied. This keeps the harness safe and non-autonomous.
    allowed = ["Read", "Grep", "Glob", "LS"]
    if with_graph:
        allowed += ["mcp__depgraph__depgraph_query"]
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--strict-mcp-config", "--mcp-config", cfg_path,
        "--allowedTools", *allowed,
        "--max-turns", str(max_turns),
    ]
    if with_graph:
        cmd += ["--append-system-prompt", NUDGE]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                          timeout=timeout)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"_parse_error": True, "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:]}


def _usage(res: dict) -> dict:
    """Aggregate token usage across iterations of one run."""
    its = res.get("usage", {}).get("iterations") or [res.get("usage", {})]
    inp = sum(i.get("input_tokens", 0) for i in its)
    cre = sum(i.get("cache_creation_input_tokens", 0) for i in its)
    rea = sum(i.get("cache_read_input_tokens", 0) for i in its)
    out = sum(i.get("output_tokens", 0) for i in its)
    return {
        "input": inp,
        "cache_creation": cre,
        "cache_read": rea,
        "output": out,
        "context_processed": inp + cre + rea,   # everything fed to the model
        "fresh_context": inp + cre,              # new content read this run
        "cost": res.get("total_cost_usd", 0.0),
        "turns": res.get("num_turns", 0),
        "error": res.get("is_error", False) or res.get("_parse_error", False),
    }


def _pct(a: float, b: float) -> str:
    if b <= 0:
        return "n/a"
    return f"{100 * (1 - a / b):+.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("prompts", nargs="+")
    ap.add_argument("--max-turns", type=int, default=15)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)

    with tempfile.TemporaryDirectory() as td:
        dep = os.path.join(td, "dep.json")
        emp = os.path.join(td, "empty.json")
        json.dump(DEP_CONFIG, open(dep, "w"))
        json.dump(EMPTY_CONFIG, open(emp, "w"))

        totals = {"with": {"cost": 0.0, "fresh": 0, "ctx": 0, "turns": 0},
                  "without": {"cost": 0.0, "fresh": 0, "ctx": 0, "turns": 0}}

        for prompt in args.prompts:
            print(f"\n{'='*70}\nTASK: {prompt}\n{'='*70}", flush=True)
            print("  running WITHOUT graph …", flush=True)
            wo = _usage(_run(repo, prompt, emp, with_graph=False,
                             max_turns=args.max_turns, timeout=args.timeout))
            print("  running WITH graph …", flush=True)
            wg = _usage(_run(repo, prompt, dep, with_graph=True,
                             max_turns=args.max_turns, timeout=args.timeout))

            def row(label, k):
                a, b = wg[k], wo[k]
                return f"  {label:<22}{b:>14,}{a:>14,}   {_pct(a, b)}"

            print(f"\n  {'metric':<22}{'WITHOUT':>14}{'WITH graph':>14}   change")
            print(f"  {'-'*60}")
            print(row("fresh context (tok)", "fresh_context"))
            print(row("total ctx processed", "context_processed"))
            print(row("output (tok)", "output"))
            print(f"  {'turns':<22}{wo['turns']:>14}{wg['turns']:>14}")
            print(f"  {'cost (USD)':<22}{wo['cost']:>14.4f}{wg['cost']:>14.4f}"
                  f"   {_pct(wg['cost'], wo['cost'])}")
            if wo["error"] or wg["error"]:
                print("  ⚠ one run errored or hit the turn/timeout cap — "
                      "treat this prompt's numbers with caution")

            totals["with"]["cost"] += wg["cost"]
            totals["without"]["cost"] += wo["cost"]
            totals["with"]["fresh"] += wg["fresh_context"]
            totals["without"]["fresh"] += wo["fresh_context"]
            totals["with"]["ctx"] += wg["context_processed"]
            totals["without"]["ctx"] += wo["context_processed"]

        w, o = totals["with"], totals["without"]
        print(f"\n{'='*70}\nTOTALS across {len(args.prompts)} task(s)\n{'='*70}")
        print(f"  fresh context : {o['fresh']:,} → {w['fresh']:,}  "
              f"({_pct(w['fresh'], o['fresh'])})")
        print(f"  total context : {o['ctx']:,} → {w['ctx']:,}  "
              f"({_pct(w['ctx'], o['ctx'])})")
        print(f"  cost (USD)    : {o['cost']:.4f} → {w['cost']:.4f}  "
              f"({_pct(w['cost'], o['cost'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
