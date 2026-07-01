# Dependency Graph Retrieval

> **Give AI a map of your codebase before it starts reading.**

---

## The Problem

When you ask an AI assistant to work on a large codebase, it has no idea where to look. So it does the only thing it can — it searches blindly. It greps for keywords, opens files that sound relevant, reads through them, finds they're wrong, opens more files, and repeats until it stumbles across what it actually needed. On a 1,600-file monorepo this can mean 20 agent turns, dozens of unnecessary file reads, and a cost that is entirely driven by wasted exploration rather than actual work.

The root cause is simple: the AI has no structural knowledge of the codebase. It doesn't know which files import which, which modules are central to a feature, or which files are guaranteed to be relevant before it reads a single byte of content.

---

## What We Are Building

**Dependency Graph Retrieval** is a system that builds a lightweight weighted map of a codebase — a dependency graph — and lets an AI query that map before touching any file.

The key insight is separating **knowing** from **reading**:

- The graph stores only **metadata**: file path, a one-line description, file type, and dependency relationships. No file content is ever stored.
- Every file in the codebase is always **visible** in the graph, so the AI can never miss a file it didn't know existed.
- When a task comes in, the AI **traverses** the graph using the query — scoring files on metadata, following weighted dependency edges, stopping when relevance falls off — and returns only the 5–20 files that actually matter.
- The AI then reads **only those files** and does the work.

This turns the navigation problem from an open-ended search into a targeted lookup. The AI goes from wandering a forest to following a map.

```
Without graph:  AI searches 1,600 files → reads ~35 wrong ones → wanders for 20 turns
With graph:     AI queries the map → reads 12 targeted files → done in 11 turns
```

> Measured across small, medium, and large repos. Full results in [7ab_test_summary.md](./7ab_test_summary.md).

---

## How It Works

The system has two phases: **build once**, **query many times**.

### Phase 1 — Build the Graph

```
Your repo
    │
    ▼
Scanner          Walks every file. Records path, type, size, hash.
                 Never reads content into the graph — metadata only.
    │
    ▼
Extractor        Per-file: extracts imports, function calls,
                 class inheritance, config references.
                 Python (stdlib ast), JS/TS, Go, Rust, Java.
    │
    ▼
Graph Builder    Resolves cross-file relationships into a directed graph.
                 Each edge carries a weight 1–10 based on coupling strength:
                 inheritance (strongest) → named imports → bare imports (weakest).
    │
    ▼
.depgraph/
  graph.json     The graph — nodes (file metadata) + weighted edges.
  graph.html     Self-contained interactive visual viewer. Open in any browser.
  cache/         Per-file extraction cache keyed by sha256.
```

### Phase 2 — Query the Graph

```
Natural language query: "how does authentication work"
    │
    ▼
Metadata scoring     Score every node on filename + description + type + symbols.
                     Cheap: no file reads. BM25-style keyword relevance.
    │
    ▼
Seed selection       Take the top-scoring nodes as traversal seeds.
    │
    ▼
Weighted traversal   Follow edges from seeds. Propagate relevance as:
                       next_score = current_score × (edge_weight / 10)
                     Stop when score drops below the adaptive cutoff.
                     Thick edges (tight coupling) propagate further.
                     Thin edges (weak coupling) stop quickly.
    │
    ▼
Result               5–20 files with: path, description, why selected, token estimate.
                     Plus: X tokens selected vs Y tokens full repo → Z% saved.
```

### Incremental Sync

The graph does not require a full rebuild when files change. Every query first runs a three-pass diff:

1. **Stat sweep** — check size and mtime for every file (no reads, ~250ms on 1,600 files)
2. **Hash confirm** — sha256 only files that stat-changed (filters out touch-without-edit)
3. **Patch in place** — re-parse changed files only; re-resolve all edges in memory

This means after editing a file, the next query automatically reflects the new state. The graph is always current.

---

## Graph Nodes and Edges

**Nodes** store only metadata — never file content:

| Field | Value |
|-------|-------|
| `path` | Relative path from repo root |
| `description` | One-line summary (heuristic from docstring/comments, or LLM-generated) |
| `file_type` | `service`, `model`, `config`, `test`, `entrypoint`, `util`, etc. |
| `mtime` / `size` | For incremental sync |
| `sha256` | Content hash for cache keying |
| `always_include` | `true` for config/env files (always pulled into results) |

**Edges** are weighted directed relationships:

| Relationship | Weight |
|---|:-:|
| Class inheritance | +4 |
| Named import, symbol used frequently | +3 |
| Named import, symbol used occasionally | +2 |
| Star / namespace import | +1 |
| Bare module import | +1 |
| Config / env dependency | max 4, node flagged `always_include` |

Weights from all relationship types between a file pair are summed and clamped to `[1, 10]`.

---

## Installation

Requires **Python 3.9+**. Uses [uv](https://docs.astral.sh/uv/):

```bash
git clone <this-repo>
cd graph
uv sync
```

Optional extras — everything works without them:

```bash
uv sync --extra treesitter   # Accurate JS/TS parsing (falls back to regex otherwise)
uv sync --extra llm          # AI-generated descriptions via --llm (needs ANTHROPIC_API_KEY)
uv sync --extra mcp          # Claude Code MCP server integration
```

<details>
<summary>Using pip instead of uv</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

</details>

---

## Quickstart

```bash
# Build the graph for any repo
uv run depgraph build /path/to/your/repo --open

# Ask which files are relevant to a task
uv run depgraph query "fix the authentication login bug" /path/to/your/repo

# Get more files for a complex cross-cutting task
uv run depgraph query "trace the full data pipeline" /path/to/your/repo --depth deep

# Inspect what changed since the last build
uv run depgraph update /path/to/your/repo
```

---

## Usage

### 1. Build the graph

```bash
uv run depgraph build /path/to/your/repo
```

Scans every file, extracts dependencies, builds the weighted graph, writes `.depgraph/` into the repo root. First build takes 10–60 seconds depending on repo size. Output:

```
Scanning /path/to/your/repo …
  found 1,604 files
Extracting dependencies …
  parsed 1,604, reused 0 from cache
Building weighted graph …
  1,604 nodes, 3,891 edges

✓ graph.json  → .depgraph/graph.json
✓ graph.html  → .depgraph/graph.html  (open in a browser)
✓ report.md   → .depgraph/report.md
```

Flags:

| Flag | Description |
|------|-------------|
| `--open` | Open `graph.html` in browser immediately after build |
| `--llm` | Use Anthropic API for richer descriptions (needs `ANTHROPIC_API_KEY`) |
| `--rebuild` | Ignore cache and re-parse every file from scratch |
| `--docs` | Include markdown/documentation files as graph nodes |

---

### 2. Query for relevant files

```bash
uv run depgraph query "how does user authentication work" /path/to/your/repo
```

Output:

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
Tokens: 2,600 loaded vs 641,600 full repo → 99.6% saved
```

Read only those files, then do the work. That is the entire workflow.

---

### 3. Control traversal depth

Use `--depth` to control how many files come back and how far the traversal reaches:

| Preset | Files | Best for |
|--------|:-----:|----------|
| `focused` | ~5 | Pinpoint bugs, single-file changes |
| `balanced` | ~8 | Most tasks — the default |
| `deep` | ~12 | Multi-module tasks, unfamiliar areas |
| `exhaustive` | ~20 | Broad refactors, "find everything touching X" |

```bash
# Default (balanced)
uv run depgraph query "where is rate limiting implemented" /path/to/repo

# For a complex task spanning many modules
uv run depgraph query "trace the full request lifecycle" /path/to/repo --depth deep

# For broad impact analysis
uv run depgraph query "everything that touches the payments module" /path/to/repo --depth exhaustive
```

---

### 4. Keep the graph current

Queries auto-sync before running — you do not need to rebuild manually:

```bash
# Edit files...
# Then query again. The graph reflects the new state automatically.
uv run depgraph query "..." /path/to/repo
```

To see what the sync found:

```bash
uv run depgraph update /path/to/repo
```

```
Sync: 2 added, 1 changed, 0 deleted
  + src/api/new_endpoint.py
  + src/models/payment.py
  ~ src/auth/login.py
Graph now 1,606 nodes, 3,894 edges.
```

---

### 5. Pack mode — paste context into any LLM

`--pack` produces a single block ready to paste into Claude.ai, ChatGPT, or any LLM chat:

```bash
uv run depgraph query "how does login work" /path/to/repo --pack > context.txt
```

The block contains:
1. One-line metadata for every file in the repo (descriptions only, no code)
2. Full content of the selected files only

---

### 6. Visual graph viewer

```bash
uv run depgraph viz /path/to/repo --open
```

The self-contained `graph.html` works offline. It shows:
- Nodes coloured by file type, sized by number of connections
- Edge thickness proportional to coupling weight
- Click any node — see its description, type, path, and all connected files
- Search box to find specific files instantly
- Physics toggle (force-directed layout on/off)

Highlight query results in the viewer:

```bash
uv run depgraph query "auth flow" /path/to/repo --open
```

---

### 7. Docker containers

If your code runs inside a running Docker container, depgraph can still reach it:

**Option 1 — `depgraph docker` command (recommended)**

```bash
# Scans /app inside the container (default path)
depgraph docker my_container

# Scan a different path inside the container
depgraph docker my_container /src

# Then query as normal — graph is stored in the extracted temp directory
depgraph query "how does auth work" /tmp/depgraph_docker_xxx/app
```

Internally this runs `docker cp` to extract the code, builds the graph on the extracted copy, and prints the exact query command to use.

**Option 2 — Volume-mounted code (zero extra steps)**

```bash
# Code is already on the host via -v mount:
docker run -v /local/code:/app myimage

# Scan from the host — depgraph doesn't know or care it's also in a container
depgraph build /local/code
```

**Option 3 — Run depgraph inside the container**

```bash
docker exec my_container pip install depgraph-retrieval
docker exec my_container python -m depgraph build /app
docker cp my_container:/app/.depgraph ./   # pull the graph out
```

> **What depgraph cannot help with:** runtime debugging — container logs, running processes, live environment variables, network traffic between containers. For those, use `docker exec`, `docker logs`, and `docker inspect`.

---

### 8. Graph statistics

```bash
uv run depgraph stats /path/to/repo
```

```
Nodes: 1,604   Edges: 3,891

File types:
  test         401
  service      312
  model        198
  config        87
  other        606

Most connected files (architectural hubs):
  142 links  src/db/session.py
   98 links  src/config/settings.py
   87 links  src/models/base.py
```

High-connectivity files are architectural hubs — they appear often in query results because many other files depend on them.

---

## MCP Compatibility

MCP (Model Context Protocol) is an open standard — not Claude-exclusive. Any tool that implements the MCP client spec can use this server without any code changes on your side.

| Tool | MCP Support | Works with depgraph? |
|------|:-----------:|:--------------------:|
| Claude Code (CLI) | Native | ✅ Yes |
| Claude Desktop | Native | ✅ Yes |
| Cursor | Added MCP support | ✅ Yes |
| Windsurf (Codeium) | Added MCP support | ✅ Yes |
| Zed editor | Added MCP support | ✅ Yes |
| Continue.dev | Added MCP support | ✅ Yes |
| GitHub Copilot | No MCP (own extension model) | ❌ No |
| OpenAI Codex CLI | No MCP | ❌ No |
| OpenAI API (direct) | No MCP | ❌ Need bridge |
| Gemini API (direct) | No MCP | ❌ Need bridge |

The config format differs slightly per tool but the content is the same — just point it at `python -m depgraph.mcp_server`. See your tool's MCP documentation for the exact config file location.

---

## Claude Code Integration

The graph can be registered as an MCP (Model Context Protocol) tool server inside Claude Code. Once registered, Claude automatically calls `depgraph_query` before reading or searching any file — turning every Claude Code session into a graph-guided navigation session.

### Register once

```bash
# Install the MCP extra
pip install -e '.[mcp]'

# Register at user scope — active in every repo, every session
claude mcp add depgraph -s user -- /path/to/graph/.venv/bin/python -m depgraph.mcp_server

# Verify
claude mcp list
```

No further setup required. Open a new `claude` session and the tools are available.

### Tools Claude gets

| Tool | What it does |
|------|-------------|
| `depgraph_query(query, path, depth)` | Returns the minimal file set for a task — paths, descriptions, why each was selected, token savings vs full repo |
| `depgraph_map(path)` | Structural overview of a repo: file count, type distribution, top hub files |
| `depgraph_build(path, rebuild)` | Force a full graph rebuild (queries auto-build on first use, so this is rarely needed) |

### What it looks like in practice

You type in Claude Code:

```
Fix the bug where JWT tokens expire immediately after login
```

Claude does:
1. Calls `depgraph_query("JWT token expiry login")` → receives 6 file paths
2. Reads those 6 files
3. Finds the bug
4. Fixes it

Without the graph, Claude would have grepped for "JWT", "token", "expiry", "login" across 1,600 files, read a dozen wrong ones, and eventually found the right ones on turn 12.

### Auto-sync during a session

Every `depgraph_query` call first runs the incremental sync. So when Claude edits a file and you ask a follow-up question, the graph already reflects the change — no manual rebuild step.

---

## Measured Results

We measure real token usage and cost with `tools/ab_token_test.py` — the same task run twice, once with the graph and once without — across three repos of different sizes. The latest sweep (symbol slicing active) was run on **three repos** of increasing size:

| Repo | Size | Without graph | With graph | Cost change |
|------|------|:---:|:---:|:---:|
| FitLLM | small (30 files) | $0.103 | $0.163 | **−58%** |
| ART | medium (377 files) | $0.285 | $0.160 | **+44% cheaper** |
| Monorepo | large (1,692 files) | $0.517 | $0.299 | **+42% cheaper** |

*(Measured on Claude Haiku so the sweep is cheap and repeatable — compare percentages, not absolute dollars.)*

### What the data shows

The turning point was **symbol slicing**: the tool now inlines the relevant code (`=== CODE ===` answer pack) instead of returning bare file paths, so the AI answers without a separate Read turn per file. That single change flipped both the **medium and large repos to net-positive in the same sweep** (+44% and +42% cheaper) — previously only the large monorepo cleared break-even.

The graph is still not a universal cost saver. It wins once there is real exploration to eliminate, and loses on tiny repos where grep already nails the answer in one pass.

| Scenario | Does the graph help? |
|---|:---:|
| Large repo, complex multi-file task | **Yes** |
| Medium repo, cross-cutting task | **Yes** |
| Tracing flows across many modules | **Yes** |
| Unfamiliar codebase, first orientation | **Yes** |
| Small repo (< ~100 files) | No |
| Simple pinpoint bug | No |

The mechanism is **eliminating exploration turns**: on ART the graph cut a 12-turn evaluation task to 7 and an attack-tracing task to 2, answering straight from the inlined slices. The small-repo penalty (FitLLM −58%) is expected and partly a measurement outlier — on a 30-file repo there is almost nothing to explore, so any graph overhead is pure loss.

> Full breakdown with per-task numbers: [7ab_test_summary.md](./7ab_test_summary.md)

---

## Project Structure

```
depgraph/
├── cli.py              Entry point: build / query / update / viz / stats commands
├── scanner.py          Walk repo, classify file types, compute sha256, record metadata
├── extractors/
│   ├── __init__.py     Dispatch table by language
│   ├── python_ast.py   Python: imports, inheritance, call frequency, docstring (stdlib ast)
│   ├── js_ts.py        JS/TS: import/export/require/extends (tree-sitter; regex fallback)
│   ├── go_lang.py      Go: import paths → package dirs, func/type defs
│   ├── rust_lang.py    Rust: mod declarations + use crate:: paths, fn/struct/trait defs
│   └── java_lang.py    Java: package-mirrored imports, class/interface/enum defs
├── slicer.py           Query-scoped symbol slicing → inline "answer pack" (per-language)
├── describe.py         Heuristic one-line descriptions from docstrings and structure
├── weights.py          Edge weight scoring: 1–10, clamped, summed across relationship types
├── graph_builder.py    Cross-file import resolution → NetworkX DiGraph; sync_graph() for patches
├── retrieve.py         Query → metadata scoring → weighted traversal → file selection + savings
├── incremental.py      Three-pass incremental sync: stat → hash → patch in place
├── store.py            graph.json serialization; sha256 extraction cache under .depgraph/cache/
├── mcp_server.py       FastMCP stdio server exposing depgraph_query / map / build to Claude Code
├── html_export.py      Self-contained offline vis-network HTML viewer
├── llm.py              Optional Anthropic API wrapper for LLM-generated descriptions
└── templates/
    └── viewer.html.tpl vis-network HTML template

tools/
└── ab_token_test.py    A/B harness: runs claude -p twice per task, diffs real token usage and cost

examples/
└── sample_app/         Small fixture (auth, models, routes, db, config) for testing queries
```

---

## Language Support

depgraph is multi-language. A language is *fully supported* when all three stages work:
its files become graph nodes, its imports build dependency edges, and query results are
sliced to the relevant symbols inline.

| Language | Nodes | Dependency edges | Symbol slicing |
|----------|:-----:|:----------------:|:--------------:|
| Python | ✅ | ✅ (stdlib `ast`) | ✅ (`ast`) |
| JavaScript / TypeScript (+ JSX/TSX) | ✅ | ✅ (tree-sitter; regex fallback) | ✅ |
| Go | ✅ | ✅ (import path → package dir) | ✅ |
| Rust | ✅ | ✅ (`mod` + `use crate::`) | ✅ |
| Java | ✅ | ✅ (package-mirrored imports) | ✅ |
| C, C++, C#, Swift, Kotlin, Scala, PHP, Objective-C | — | — | ✅ (sliced if selected) |

The slicer understands all brace-family languages, so the moment an extractor is added for
one, slicing already works. Non-`ast` extractors are regex-based: robust for the common
import/definition forms, and every slice carries line ranges so the agent can fall back to a
full read if needed.

### Adding a language

1. Create `depgraph/extractors/your_lang.py` implementing `extract(text) -> ExtractResult`
   (imports, `defined_symbols`, optional inheritance/usage counts).
2. Register the extension in `scanner.py` (`CODE_EXTENSIONS`) and the dispatch in
   `extractors/__init__.py`; add a `_resolve_<lang>` in `graph_builder.py` for edges.

Everything else — retrieval, slicing, incremental sync, HTML viewer, MCP tools — works
without modification.

---

## Requirements

| Package | Required | Purpose |
|---------|:--------:|---------|
| `networkx` | Yes | Graph data structure and traversal |
| `mcp` | For Claude Code integration | MCP stdio server |
| `tree-sitter` + `tree-sitter-languages` | Optional | More accurate JS/TS extraction (Go/Rust/Java/Python need nothing extra) |
| `anthropic` | Optional | LLM-generated file descriptions |

Python 3.9 or later. No database, no server, no external services required for core functionality.

---

## Contributors

| Name | Role |
|------|------|
| [Abhishek Jois](https://github.com/abhishek-jois) | Creator — architecture, design, and direction |
| [Claude](https://claude.ai) (Anthropic) | AI pair programmer — implementation, incremental sync, MCP integration, A/B harness |

---

## License

MIT License — see [LICENSE](./LICENSE) for the full text.

Copyright (c) 2026 Abhishek Jois
