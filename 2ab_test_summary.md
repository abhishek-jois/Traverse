## Test Run 2 — After All Four Fixes

This is the second A/B test, run after applying all four cost-reduction fixes:
- **Fix 1** — Claude calls `depgraph_query` exactly once per question (no re-querying)
- **Fix 2** — Dynamic file count using score knee detection (no fixed cap)
- **Fix 3** — Removed `depgraph_map` and `depgraph_build` from MCP (smaller schema per turn)
- **Fix 4** — Claude reads only files the graph returns (no extra exploration)

**Repo:** `/data/abhishek/ART` (377 scannable files — AI training framework)
**Previous test repo:** monorepo (~1,600 files)

---

## The Raw Results

### Task 1 — "Where is model checkpointing implemented?" (Pinpoint)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context (new tokens read) | 1,642 tok | 969 tok | +41% better |
| Total context processed | 25,026 tok | 39,681 tok | −59% worse |
| Output tokens | 389 tok | 360 tok | similar |
| Turns | 11 | 10 | similar |
| **Cost (USD)** | **$0.1438** | **$0.3133** | **−118% MORE expensive** |

**Plain English:** Graph found the right file faster (41% less reading) but the MCP schema overhead made total context balloon. For a simple pinpoint question, the overhead still costs more than it saves.

---

### Task 2 — "How does the training loop work?" (Medium)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context (new tokens read) | 1,027 tok | 20,477 tok | more upfront |
| Total context processed | 44,677 tok | 42,898 tok | +4% better |
| Output tokens | 791 tok | 1,774 tok | richer answer |
| Turns | **18** | **10** | **much better** |
| **Cost (USD)** | **$0.3338** | **$0.2563** | **+23% CHEAPER** |

**Plain English:** The graph returned more files upfront (higher fresh context) but Claude understood the answer in 10 turns instead of 18. The upfront reading paid off — total context dropped and cost fell 23%. Graph won.

---

### Task 3 — "Trace the full data pipeline from input loading to model training" (Cross-cutting)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context (new tokens read) | 1,893 tok | 19,864 tok | more upfront |
| Total context processed | 78,172 tok | 74,896 tok | +4% better |
| Output tokens | 3,172 tok | 2,722 tok | similar |
| Turns | **21** | **15** | **much better** |
| **Cost (USD)** | **$0.6354** | **$0.5293** | **+17% CHEAPER** |

**Plain English:** Same pattern as Task 2. More reading upfront, far fewer turns, lower total context. Without the graph Claude wandered for 21 turns. With it, done in 15. Graph won.

---

## Totals — 3 Tasks Combined

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | 4,562 tok | 41,310 tok | more upfront |
| Total context | 147,875 tok | 157,475 tok | −6% worse |
| **Cost (USD)** | **$1.1131** | **$1.0989** | **+1% better (near equal)** |

---

## Compared to Test Run 1

| Test | Repo size | Overall cost change |
|------|:---------:|:-------------------:|
| Run 1 (before fixes) | ~1,600 files | **+18% MORE expensive** |
| Run 2 (after fixes) | 377 files | **+1% (essentially equal)** |

The fixes moved the graph from being a clear net cost increase (+18%) to cost-neutral (+1%). That is a real improvement, but the graph has not yet crossed into net savings territory overall.

---

## What Changed Between Run 1 and Run 2

**Why total context is still slightly higher WITH graph:**
The MCP schema (even with just one tool now) still re-processes every turn. On Task 1 — 10 turns × schema tokens = overhead that dominates a simple answer.

**Why Tasks 2 and 3 now win:**
The dynamic file count returned more files upfront for multi-module questions (score stayed high across many files, so the knee detector let more through). Claude loaded all relevant context in turn 1 instead of discovering files across 18–21 turns. Fewer turns = less accumulated total context = lower cost.

**Why Task 1 still loses:**
A pinpoint question ("where is X") doesn't need 10 turns regardless. The graph adds one query turn + schema overhead to a task Claude could answer in 2–3 turns with a simple grep. The overhead is not recovered.

---

## The Honest Takeaway After Fixes

| Situation | Does the graph help? |
|-----------|---------------------|
| Cross-cutting task ("trace the pipeline") | **Yes — 21→15 turns, 17% cheaper** |
| Multi-module question ("how does X work") | **Yes — 18→10 turns, 23% cheaper** |
| Pinpoint lookup ("where is X") | No — overhead still dominates |
| Small repo (< 100 files) | Not tested here, but likely still no |

**The pattern is now clear:** the graph pays off when the task requires Claude to discover multiple files across modules. In those cases, loading the right files upfront in one query costs less than discovering them across 15–20 exploration turns. The dynamic file count was the key fix — it returned the right volume of files for complex queries instead of a fixed 8.

**The remaining problem:** simple pinpoint tasks still hurt. The ideal next step is routing — only call the graph when the query is detectable as cross-cutting, skip it when grep would find the answer in one shot.

---

## What the Numbers Mean (Plain English)

**Fresh context went up** — the graph returns more files at once. Claude reads them all in turn 1. This looks expensive but it is paid once.

**Total context went down on winning tasks** — because Claude finishes in fewer turns. Each turn re-processes everything accumulated so far. Fewer turns = exponentially less total context.

**Cost is the true judge** — it accounts for all three token buckets at their real prices. Fresh context and total context can move in opposite directions; cost is the net result.
