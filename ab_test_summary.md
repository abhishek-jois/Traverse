## What We Built (Before the Test)

### The Core Idea
Normally when you ask Claude to do something in a big codebase, Claude has to search through many files to find what's relevant — reading lots of files it doesn't need. The graph is a **map of the whole codebase**: every file is a node, and edges connect files that depend on each other (imports, calls, inheritance). Claude can query the map first, get the 5–12 most relevant files, and read only those.

### What We Built, Step by Step

**1. The graph itself (`depgraph/`)**
- Scans every file in a repo, records metadata only (path, description, file type, size) — no content
- Extracts dependencies: "file A imports file B" → edge with a weight 1–10
- Saves a `graph.json` + a visual `graph.html` you can open in a browser

**2. Built it for two repos**
- `/data/abhishek/FitLLM` (~30 files, small fitness app)
- `/data/abhishek/monorepo` (~1,600 files, large mixed repo)

**3. Wired it into your Claude Code as a plugin (MCP server)**
- Every `claude` session now has tools: `depgraph_query("find auth logic")` → returns the relevant files
- Registered at user level so it works in any repo, any session

**4. Added depth presets** — control how many files come back:
- `focused` = ~5 files | `balanced` = ~8 (default) | `deep` = ~12 | `exhaustive` = ~20

**5. Incremental sync** — when you edit files, the graph updates only the changed parts automatically. No full rebuild needed. A no-op check takes ~250ms on 1,600 files.

**6. A/B test harness (`tools/ab_token_test.py`)**
- Runs the same task twice: once with graph, once without
- Uses `claude -p` (headless Claude) and captures real token usage + cost from Claude's API

---

## The A/B Test Results

### FitLLM (30 files, 1 task)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context (new tokens read) | 17,500 tok | 1,500 tok | **−91%** |
| Total context processed | ~40,000 tok | ~55,000 tok | +38% |
| Turns | ~6 | ~10 | worse |
| **Cost (USD)** | **$0.41** | **$0.65** | **+58% MORE** |

**Plain English:** The graph successfully found the right files (91% less reading). But because the graph tool schema gets re-processed every single turn, and Claude used more turns, the total cost went up. Small repo = the overhead isn't worth it.

---

### Monorepo (1,600 files, 3 tasks total)

#### Task 1 — "Explain the authentication flow"

| Metric | WITHOUT | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | ~12k tok | ~11k tok | ~flat |
| Total context | ~90k tok | ~110k tok | +22% |
| Turns | 8 | 9 | similar |
| **Cost** | **~$0.60** | **~$0.73** | **+22% MORE** |

**Plain English:** The graph didn't help much — Claude found the auth files fine on its own with grep. The graph added overhead without reducing reading.

---

#### Task 2 — "How does the API client work?"

| Metric | WITHOUT | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | ~8k tok | ~30k tok | +275% |
| Total context | ~60k tok | ~135k tok | +125% |
| Turns | 5 | 12 | much worse |
| **Cost** | **~$0.45** | **~$1.01** | **+125% MORE** |

**Plain English:** Worst case. The keyword "API client" matched too many files in the graph. Claude was sent to read ~7x more files than needed, then explored on top of that. Over-fetching blew up cost.

---

#### Task 3 — "Trace the full data pipeline end-to-end"

| Metric | WITHOUT | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | ~35k tok | ~20k tok | −43% |
| Total context | ~280k tok | ~170k tok | −39% |
| Turns | **20** | **11** | **much better** |
| **Cost** | **~$0.90** | **~$0.57** | **−33% CHEAPER** |

**Plain English:** The graph won here. This task spans many files across the whole repo — without the graph, Claude wandered for 20 turns. With the graph, it got the right files upfront and finished in 11 turns. This is the graph's home turf.

---

### Overall Monorepo Totals (3 tasks combined)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | ~55k tok | ~61k tok | +11% |
| Total context | ~430k tok | ~515k tok | +20% |
| **Cost** | **$1.95** | **$2.31** | **+18% MORE** |

---

## The Honest Takeaway (in simple words)

| Situation | Does the graph help? |
|-----------|---------------------|
| Small repo (<100 files) | No — overhead > savings |
| Simple pinpoint task ("find function X") | No — grep already wins |
| Cross-cutting task ("trace this whole flow") | **Yes — fewer turns, less wandering** |
| Over-broad query ("API client" in big repo) | No — over-fetches, costs more |
| Unfamiliar large repo + multi-file question | **Yes — this is where it shines** |

The graph's real value isn't saving tokens on average — it's **preventing Claude from going down 20-turn rabbit holes** on complex multi-file questions. Task 3 is the proof.
