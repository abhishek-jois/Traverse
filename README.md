# Dependency Graph Retrieval

**Smarter context for smarter AI** — *Every file visible. Only what matters loaded.*

Instead of dumping an entire codebase into an AI's context window — or hoping a keyword search finds the right file — Dependency Graph Retrieval builds a **lightweight weighted map** of your repo. When you ask a question, it traverses that map and returns only the 5–20 files that actually matter for the task. The rest stay out of context.

```
Without graph:  Claude searches 1,600 files → reads ~35 wrong ones → wanders for 20 turns
With graph:     Claude queries the map → reads 12 targeted files → done in 11 turns
```

> Measured on a real 1,600-file monorepo. See [A/B test results](./ab_test_summary.md).

---

## How It Works

```
Repo files
    │
    ▼
[ Scanner ]  ──  walks every file, records metadata only
                 (path, description, type, size, hash)
    │
    ▼
[ Extractor ]  ──  per-file: imports, calls, inheritance, config deps
    │
    ▼
[ Graph Builder ]  ──  resolves cross-file imports → weighted edges (1–10)
                        nodes = file metadata, never full content
    │
    ▼
[ graph.json + graph.html ]  ──  NetworkX graph + interactive browser viewer
    │
    ▼
[ Query ]  ──  score all nodes on metadata (cheap)
               → seed from top matches
               → follow thick edges, stop at cutoff
               → return 5–20 relevant files + token savings
```

- **Nodes = files** holding only metadata (path, one-line description, file type, last-modified, size). The full content is never stored in the graph — keeping it tiny so every file is always *visible*.
- **Edges = relationships** (imports, function calls, class inheritance, config dependencies), each weighted **1–10** by coupling strength.
- **Query = traversal**: score files on metadata, follow thick edges, stop when relevance drops below a cutoff, return the minimal relevant set.

---

## Features

- **Language support** — Python (stdlib `ast`) + JavaScript/TypeScript (tree-sitter when available, regex fallback). Extractor dispatch is built to extend.
- **Depth presets** — `focused` (~5 files) / `balanced` (~8, default) / `deep` (~12) / `exhaustive` (~20). Switch with `--depth`.
- **Incremental sync** — stat-only sweep on every query detects changed files, re-parses only those, patches the graph in place. No full rebuild needed. ~250ms on 1,600 files when nothing changed.
- **Interactive HTML viewer** — self-contained, offline-ready. Nodes coloured by type, sized by connectivity; edge thickness = weight; click any node for metadata + neighbours; search box; physics toggle.
- **Claude Code plugin (MCP)** — registers as a tool server so any Claude Code session can call `depgraph_query` and `depgraph_map` before reading files. Works across every repo.
- **Pack mode** — `query --pack` emits every file's metadata + selected files' full contents as a single ready-to-paste context block.
- **LLM descriptions** — `build --llm` uses the Anthropic API for richer one-line descriptions; falls back to heuristics with no key.
- **Token savings reporting** — every query shows selected tokens vs full-repo tokens and the % saved.

---

## Install

Requires Python 3.9+. Uses [uv](https://docs.astral.sh/uv/) — one command:

```bash
uv sync
```

Optional extras (everything works without them):

```bash
uv sync --extra treesitter   # accurate JS/TS parsing (regex fallback otherwise)
uv sync --extra llm          # AI descriptions via --llm  (needs ANTHROPIC_API_KEY)
uv sync --extra mcp          # Claude Code MCP server integration
```

<details>
<summary>Using pip instead</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

</details>

---

## Quick Start

```bash
# 1. Build the graph for your repo (takes 10–60s depending on size)
uv run depgraph build /path/to/your/repo --open

# 2. Ask which files a task needs
uv run depgraph query "fix the authentication login bug" /path/to/your/repo

# 3. Get more files for a complex task
uv run depgraph query "trace the full data pipeline" /path/to/your/repo --depth deep

# 4. Sync after editing files (or just query again — it auto-syncs)
uv run depgraph update /path/to/your/repo
```

---

## Usage Guide

This section walks through the full workflow from zero to using the graph effectively.

### Step 1 — Build the graph for your repo

```bash
uv run depgraph build /path/to/your/repo
```

This scans every file, extracts imports and dependencies, builds a weighted graph, and writes it to `/path/to/your/repo/.depgraph/`. First build takes 10–60 seconds depending on repo size. Subsequent builds are fast (only changed files re-parsed).

Add `--open` to immediately open the visual graph in your browser:

```bash
uv run depgraph build /path/to/your/repo --open
```

Add `--llm` for richer AI-generated descriptions (requires `ANTHROPIC_API_KEY`):

```bash
ANTHROPIC_API_KEY=sk-... uv run depgraph build /path/to/your/repo --llm
```

---

### Step 2 — Query for relevant files

Describe your task in plain English. The graph finds the relevant files without reading any of them:

```bash
uv run depgraph query "how does user authentication work" /path/to/your/repo
```

Example output:

```
Query: how does user authentication work
────────────────────────────────────────────────────────────
  src/auth/login.py
      [service] Handles login, JWT issuance, and session creation
      why: direct match on 'authentication' · ~1,200 tok
  src/models/user.py
      [model] User schema, password hashing, roles
      why: imported by login.py (weight 8) · ~800 tok
  src/middleware/auth.py
      [middleware] JWT validation middleware for protected routes
      why: neighbour of login.py via shared token logic · ~600 tok
────────────────────────────────────────────────────────────
Selected 3 of 1,604 files (traversal cutoff 0.21)
Tokens: 2,600 loaded vs 640,000 full repo → 99.6% saved
```

**Read only those files**, then work on your task. That's the whole workflow.

---

### Step 3 — Tune depth if needed

If the first result feels incomplete (missing a file you know is relevant), increase depth:

```bash
# Default: ~8 files
uv run depgraph query "trace the full data pipeline" /path/to/repo

# More files: ~12
uv run depgraph query "trace the full data pipeline" /path/to/repo --depth deep

# Maximum: ~20 files — good for broad refactors
uv run depgraph query "find everything touching the payment module" /path/to/repo --depth exhaustive
```

---

### Step 4 — Keep the graph current

You do **not** need to rebuild manually after editing files. Every query auto-syncs:

```bash
# Edit some files in your repo...
# Then just query again — the graph reflects your changes automatically
uv run depgraph query "..." /path/to/repo
```

To inspect what changed since the last build:

```bash
uv run depgraph update /path/to/repo
# Sync: 2 added, 1 changed, 0 deleted
#   + src/api/new_endpoint.py
#   + src/models/payment.py
#   ~ src/auth/login.py
```

---

### Step 5 — Explore the visual graph

Open the HTML viewer anytime to see the full structure of your repo:

```bash
uv run depgraph viz /path/to/repo --open
```

The viewer shows:
- **Nodes** coloured by file type (service, model, config, test, etc.) and sized by how many files connect to them
- **Edges** with thickness proportional to coupling weight — thick edges = tight dependency
- **Click any node** to see its description, type, path, and all its connections
- **Search box** to find specific files

To see a query result highlighted in the viewer:

```bash
uv run depgraph query "auth flow" /path/to/repo --open
```

---

### Pack mode — ready-to-paste context

For pasting into any LLM chat (ChatGPT, Claude.ai, etc.), `--pack` emits a single block with every file's metadata plus the full contents of the selected files:

```bash
uv run depgraph query "how does login work" /path/to/repo --pack > context.txt
```

The output block is structured as:
1. Metadata for every file in the repo (tiny — descriptions only, no code)
2. Full content of the selected files

Copy-paste the whole thing into your LLM chat and ask your question.

---

### Using with Claude Code (MCP)

If you registered the MCP server (see [Claude Code Integration](#claude-code-integration-mcp-plugin) below), Claude does this automatically inside any `claude` session:

1. You type: *"Fix the login bug where tokens expire too soon"*
2. Claude calls `depgraph_query("token expiry login bug")` → gets 6 file paths
3. Claude reads those 6 files only
4. Claude fixes the bug

You don't do anything differently. The graph runs behind the scenes, replacing Claude's blind grep/search phase with a targeted lookup.

---

### Check graph stats

See what's in the graph: file counts by type, most-connected hub files, edge confidence:

```bash
uv run depgraph stats /path/to/repo
```

```
Nodes: 1,604   Edges: 3,891

File types:
  service      312
  model        198
  config        87
  test         401
  other        606

Most connected files (god nodes):
  142 links  src/db/session.py
   98 links  src/config/settings.py
   87 links  src/models/base.py
   ...
```

High link-count files ("god nodes") are the architectural hubs. They appear often in query results because many other files depend on them.

All outputs go to `/path/to/your/repo/.depgraph/`:

| File | Contents |
|------|----------|
| `graph.html` | Self-contained interactive viewer — open in any browser, works offline |
| `graph.json` | Serialized NetworkX graph (nodes + weighted edges) |
| `report.md` | Summary: most-connected files, cross-cutting configs, counts |
| `cache/` | Per-file extraction cache (keyed by sha256 for incremental rebuilds) |

---

## CLI Reference

### `depgraph build`

Scan a repo and build the dependency graph.

```bash
depgraph build [path] [--llm] [--rebuild] [--docs] [--open]
```

| Flag | Description |
|------|-------------|
| `path` | Repo root to scan (default: current directory) |
| `--llm` | Use Anthropic API for richer descriptions (needs `ANTHROPIC_API_KEY`) |
| `--rebuild` | Ignore extraction cache and re-parse every file from scratch |
| `--docs` | Include documentation files as graph nodes |
| `--open` | Open `graph.html` in browser when done |

---

### `depgraph query`

Traverse the graph to find files relevant to a task.

```bash
depgraph query "<task>" [path] [--depth PRESET] [--max N] [--cutoff F] [--pack] [--open] [--no-sync]
```

| Flag | Description |
|------|-------------|
| `query` | Task or question in natural language |
| `path` | Repo root (default: current directory) |
| `--depth` | Traversal reach — `focused` / `balanced` / `deep` / `exhaustive` (default: `balanced`) |
| `--max N` | Hard cap on files returned (overrides depth preset) |
| `--cutoff F` | Manual traversal cutoff 0–1 (default: size-adaptive). Higher = shallower |
| `--pack` | Print all-node metadata + selected files' full contents (ready to paste to an LLM) |
| `--open` | Regenerate `graph.html` with selected files highlighted, then open it |
| `--no-sync` | Skip incremental sync before querying (faster; use if repo hasn't changed) |

**Depth presets explained:**

| Preset | Files returned | Traversal reach | Best for |
|--------|:-:|:-:|---------|
| `focused` | ~5 | Tightest | Pinpoint bugs, single-file questions |
| `balanced` | ~8 | Default | Most tasks — good starting point |
| `deep` | ~12 | Wider | Multi-module tasks, unfamiliar areas |
| `exhaustive` | ~20 | Maximum | Broad refactors, "find everything touching X" |

---

### `depgraph update`

Incrementally sync the graph with filesystem changes (without a full rebuild).

```bash
depgraph update [path]
```

Shows added (`+`), changed (`~`), and deleted (`-`) files. Queries auto-sync, so this is mainly for inspecting what changed.

---

### `depgraph viz`

Regenerate and optionally open the HTML viewer.

```bash
depgraph viz [path] [--open]
```

---

### `depgraph stats`

Print graph statistics: node count, edge count, file type breakdown, most-connected files, edge confidence.

```bash
depgraph stats [path]
```

---

## Claude Code Integration (MCP Plugin)

The graph can be wired directly into your Claude Code session as an MCP tool server, giving Claude access to `depgraph_query` and `depgraph_map` in any repo it works in.

### Register once (user scope — available in every repo)

```bash
# Install the MCP extra first
pip install -e '.[mcp]'

# Register with Claude Code
claude mcp add depgraph -s user -- /path/to/graph/.venv/bin/python -m depgraph.mcp_server
```

Verify it's registered:

```bash
claude mcp list
```

### What Claude gets

Three tools exposed to the agent:

| Tool | What it does |
|------|-------------|
| `depgraph_query(query, path, depth)` | Returns the minimal file set for a task — paths, descriptions, why each was chosen, token savings |
| `depgraph_map(path)` | Structural overview: file count, type mix, top hub files — good for orientation in a new repo |
| `depgraph_build(path, rebuild)` | Force a full graph rebuild (queries auto-build, so rarely needed) |

### Claude Code skill (auto-trigger)

A skill file at `~/.claude/skills/depgraph/SKILL.md` teaches Claude to call `depgraph_query` before grepping or reading broadly. When Claude encounters an unfamiliar or large repo, the skill activates automatically and routes the query through the graph first.

### How queries auto-sync

Every `depgraph_query` or `depgraph_map` call first runs a cheap incremental sync:

1. Stat-only sweep (size + mtime) across all files — ~250ms on 1,600 files
2. Re-hashes only files that stat-changed (sha256 confirm)
3. Re-parses only the changed files, updates only those graph nodes + edges
4. Returns the up-to-date graph

This means after Claude edits a file, the next query automatically reflects the new state — no manual rebuild.

---

## Incremental Sync (Under the Hood)

`depgraph/incremental.py` implements a three-pass diff:

```
1. stat_only scan   →  find size/mtime changes (no reads)
2. sha256 confirm   →  exclude touch-without-edit (no false re-parses)
3. patch in place   →  re-parse changed files only; re-resolve ALL edges in memory
                       (so a new file can satisfy an unchanged file's import)
```

The full edge re-resolution step (pass 3) ensures consistency: if you add a new file that another file was trying to import, the edge appears without touching the importing file.

---

## A/B Test Findings

We measured real Claude Code token usage and cost on a 1,600-file monorepo across 3 representative tasks — running each with and without the graph (via `tools/ab_token_test.py`).

### Summary table

| Task | Without graph | With graph | Change |
|------|:-:|:-:|:-:|
| "Explain the auth flow" (pinpoint) | $0.60 / 8 turns | $0.73 / 9 turns | +22% cost |
| "How does the API client work?" (keyword match fails) | $0.45 / 5 turns | $1.01 / 12 turns | +125% cost |
| "Trace the full data pipeline" (cross-cutting) | $0.90 / **20 turns** | $0.57 / **11 turns** | **−33% cost** |
| **Monorepo total** | **$1.95** | **$2.31** | **+18% cost** |

### Honest takeaway

| Situation | Graph helps? |
|-----------|:-----------:|
| Large repo + complex multi-file task | **Yes — fewer turns, no rabbit holes** |
| Tracing a flow across many modules | **Yes — surfaces the right files upfront** |
| Small repo (< 100 files) | No — overhead > savings |
| Pinpoint task a grep already finds | No — graph adds latency, not value |
| Broad/ambiguous query | No — over-fetches, costs more |

The graph's real value is **bounding worst-case exploration** (task 3: 20 turns → 11 turns) and **never missing a file** on cross-cutting questions. It is not an average token saver — the "96% saved" figure is a theoretical ceiling (selected vs whole repo), not realized end-to-end.

> Full numbers and analysis: [ab_test_summary.md](./ab_test_summary.md)

---

## Project Layout

```
depgraph/
├── scanner.py          Walk repo, classify file type, compute hash, record metadata
├── extractors/
│   ├── __init__.py     Dispatch by file extension
│   ├── python_ast.py   stdlib ast: imports, class bases, call frequency, docstring
│   └── js_ts.py        tree-sitter (import/export/require/extends); regex fallback
├── describe.py         Heuristic one-line descriptions (LLM-upgradable via llm.py)
├── weights.py          Edge-weight scoring 1–10 (inheritance > named imports > config)
├── graph_builder.py    Cross-file import resolution → NetworkX DiGraph + sync_graph()
├── retrieve.py         Query → metadata scoring → weighted traversal → file selection
├── incremental.py      Incremental sync: stat diff → sha confirm → patch in place
├── store.py            graph.json serialization + sha256 extraction cache
├── mcp_server.py       FastMCP stdio server (depgraph_query / map / build)
├── html_export.py      Self-contained vis-network HTML viewer
├── llm.py              Optional Anthropic API wrapper for descriptions
├── cli.py              CLI: build / query / update / viz / stats
└── templates/
    └── viewer.html.tpl vis-network template

tools/
└── ab_token_test.py    A/B harness: measures real Claude Code token cost with vs. without graph

examples/
└── sample_app/         Small multi-file fixture (auth, models, routes, db, config)
```

---

## Edge Weight Reference

| Relationship | Weight added |
|---|:-:|
| Class inheritance (`class A extends B`) | +4 |
| Named import (symbol used frequently) | +3 |
| Named import (symbol used occasionally) | +2 |
| Star / namespace import | +1 |
| Config / env dependency | capped at 4, node flagged `always_include` |
| Bare module import | +1 |

Weights are summed across all relationship types between a file pair and clamped to `[1, 10]`. During traversal, relevance decays as `score × (weight / 10)` per hop and stops when it falls below the adaptive cutoff — so only genuinely coupled files propagate relevance.

---

## Extending to New Languages

Add a new extractor under `depgraph/extractors/`:

1. Create `depgraph/extractors/your_lang.py` implementing `extract(node) -> ExtractionResult`.
2. Register the file extensions in `depgraph/extractors/__init__.py`'s dispatch table.
3. The rest (graph building, retrieval, HTML viewer, MCP tools) works automatically.

---

## Requirements

| Dependency | Required | Purpose |
|---|:-:|---|
| `networkx` | Yes | Graph data structure and algorithms |
| `mcp` | For MCP server | Claude Code tool integration |
| `tree-sitter` + `tree-sitter-languages` | Optional | Accurate JS/TS parsing |
| `anthropic` | Optional | LLM-generated descriptions (`--llm`) |

Python 3.9+. No other hard dependencies.
