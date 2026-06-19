## Test Run 4 — Generalist Agent Monorepo (1,199 files)

**Repo:** `/data/generalist_agent_monorepo`
**Structure:** Python agent backend (Agent Zero) + Next.js frontend — 1,199 scannable files
**All nine fixes active** (same as Run 3)

This is the largest repo tested so far. Results reveal a new problem that did not appear
at 362 files (ART) or 30 files (FitLLM).

---

## Raw Results

### Task 1 — "Where is the agent tool registry and tool execution implemented?" (Pinpoint)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context (new tokens read) | 3,022 tok | 3,084 tok | −2% (same) |
| Total context processed | 27,256 tok | 38,502 tok | −41% WORSE |
| Output tokens | 471 tok | 579 tok | −23% more output |
| Turns | 12 | **22** | 10 extra turns |
| **Cost (USD)** | **$0.1541** | **$0.3254** | **−111% MORE expensive** |

**What happened:** This is a pinpoint query — Fix 1 routing in real use would classify this
as "grep tier" and skip the graph entirely. But the test NUDGE forces a graph call. The
graph returned files, Claude spent 22 turns vs 12 WITHOUT. Fresh context is identical
(same amount of code was read), but the graph tool response itself (~2,000–3,000 tok of
metadata and file paths) was re-processed on all 22 turns as cache-read tokens. That
accumulated overhead is what doubled the cost.

---

### Task 2 — "How does the agent manage memory and conversation history?" (Medium)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context (new tokens read) | 824 tok | 7,880 tok | −856% MORE reading |
| Total context processed | 40,953 tok | 43,858 tok | −7% WORSE |
| Output tokens | 1,192 tok | 1,379 tok | −16% more output |
| Turns | 12 | 11 | similar |
| **Cost (USD)** | **$0.2557** | **$0.2942** | **−15% MORE expensive** |

**What happened:** The graph loaded 9.5× more fresh context (7,880 vs 824 tokens) — it
returned several files up front about memory management across both the Python backend
and the Next.js frontend. Claude read them all. Total context is only 7% worse, and
turns are similar. The cost increase (15%) is mostly from the larger fresh-context read
in turn 1. This result is in the "not worth it" range but not a disaster — the graph
returned relevant files, just more than needed.

---

### Task 3 — "Trace the complete request flow from user message through agent processing, LLM calls, and tool execution to final response" (Cross-cutting)

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context (new tokens read) | 4,233 tok | 1,848 tok | +56% BETTER |
| Total context processed | 23,884 tok | **97,762 tok** | −309% MUCH WORSE |
| Output tokens | 1,620 tok | 4,543 tok | −180% much more output |
| Turns | **2** | **35** | 33 extra turns |
| **Cost (USD)** | **$0.5112** | **$0.9342** | **−83% MORE expensive** |

**What happened — two very different runs:**

WITHOUT graph: Claude found the CONTEXT.md / README.md which already contains a
high-level architecture overview. It used that to give a surface-level answer in 2
turns. Cheap ($0.51) but likely shallow — it described the architecture from docs,
not from tracing actual code.

WITH graph: Claude got actual file paths back, tried to trace real code through the
codebase, hit the complexity of a 1,199-file cross-language repo, and spent 35 turns
working through it. The graph response (file list + metadata) got re-processed on every
one of those 35 turns. Total context ballooned to 97,762 tokens. Quality of the answer
was likely much higher, but at 83% more cost.

**This is a quality vs cost tradeoff, not a pure waste.** The WITHOUT graph answer was
quick and cheap precisely because it did not actually trace the code — it summarised
from docs. The WITH graph answer traced real implementation across multiple files.

---

## Totals — 3 Tasks Combined

| Metric | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | 8,079 tok | 12,812 tok | −59% worse |
| Total context | 92,093 tok | 180,122 tok | −96% worse |
| **Cost (USD)** | **$0.9209** | **$1.5538** | **−69% MORE expensive** |

The graph is a clear net negative on this monorepo: 69% more expensive overall.

---

## Compared to All Previous Runs

| Run | Repo | Files | Task type | Cost change |
|-----|------|:-----:|-----------|:-----------:|
| Run 1 (0 fixes) | Monorepo | ~1,600 | 3 mixed | **+18% worse** |
| Run 2 (4 fixes) | ART | 377 | 3 mixed | **+1% neutral** |
| Run 3 — ART (9 fixes) | ART | 362 | 1 complex | **+27% cheaper** |
| Run 3 — FitLLM (9 fixes) | FitLLM | 30 | 1 complex | **−26% worse** |
| **Run 4 (9 fixes)** | **Monorepo** | **1,199** | **3 mixed** | **−69% worse** |

The monorepo is the worst result yet despite having all nine fixes applied.

---

## Root Cause: Scale Breaks the Current Architecture

The ART repo (362 files) showed the graph winning 27%. The same nine fixes on a
1,199-file repo produce 69% cost increase. What changed?

### Problem 1: Graph response length scales with repo size

In a large repo, the graph returns more files (more propagation paths, more relevant
nodes). Each file in the result adds ~80–120 tokens of metadata (path + description +
why + token count). For 8–10 returned files: ~1,000 tokens in the response.

That 1,000-token graph response gets re-processed on EVERY subsequent turn as
cache-read tokens. On Task 3 (35 turns), that is 35 × 1,000 = 35,000 extra tokens
processed — before counting any actual file reads. In a small repo with fewer turns,
this is negligible. At 35 turns it dominates.

### Problem 2: Fix 3 (inline content) can backfire at scale

Fix 3 inlines file content when selected_tokens ≤ 6,000. On the monorepo, the graph
may return fewer files than the token cap, causing content to be inlined. But that
inline content — now embedded in the tool response — also gets re-processed on every
subsequent turn. For a 35-turn run, a 3,000-token inline response costs
35 × 3,000 = 105,000 extra cache-read tokens.

### Problem 3: Cross-language monorepos scatter relevance

This repo has Python (agent) and TypeScript (web frontend). Dependency edges are only
tracked within each language. A query that spans the full stack (Task 3) cannot follow
real edges from TypeScript component → Python endpoint → Python tool. The graph returns
good Python files OR good TypeScript files but cannot connect them. Claude then spends
many turns bridging the gap manually.

### Problem 4: Docs short-circuit WITHOUT-graph runs

In a well-documented repo, WITHOUT graph can answer many questions by reading README /
CONTEXT.md in 1–2 turns. This makes the WITHOUT-graph baseline extremely cheap even
for complex queries. The graph cannot compete with a 2-turn doc-read even when it
returns better technical depth.

---

## New Issues Discovered

### Issue 10: Graph response grows with repo size — needs compression

**Current:** Each selected file gets: path + file_type + description + why + token count.
For 8 files this is ~800–1,200 tokens in the tool response.
**Fix:** Compress to just path + one-line reason. Remove `file_type`, `token count`, and
trim `why` to 5 words max. Target: 30–50 tokens per file instead of 100–150.
**Impact:** 1,200 → 300 tokens per graph response. At 35 turns that saves 31,500
cache-read tokens.

### Issue 11: Inline content (Fix 3) threshold is too high for large repos

**Current:** Inline if selected_tokens ≤ 6,000.
**Fix:** Lower to 2,500 tokens on repos > 500 files. Or disable entirely and let Claude
make targeted Read calls — targeted reads don't re-process on every turn the way an
inline tool response does.
**Impact:** Prevents the inline response from ballooning per-turn re-processing cost.

### Issue 12: Turn cap needed per individual run

**Current:** --max-turns 20 but Task 3 WITH graph ran 35 turns.
The harness `num_turns` may count tool calls within a turn, not conversation turns.
Or the cap is not being enforced strictly.
**Fix:** Add a wall-clock and turn-count check in the harness to ensure hard stops.

---

## The Honest Takeaway After Run 4

| Repo size | Task type | Graph result |
|-----------|-----------|:------------:|
| < 80 files | Any | Skip graph (Fix 7 fires, but overhead remains) |
| 80–400 files | Pinpoint | Skip graph (Fix 1 routing → grep tier) |
| 80–400 files | Multi-module or complex | **Graph wins (+17% to +27% cheaper)** |
| > 500 files | Any via NUDGE-forced call | Graph loses (response overhead scales) |
| > 500 files | In real use with Fix 1 routing | Unknown — not tested without NUDGE |

**The graph's sweet spot is 80–400 files with multi-module or complex tasks.**

Above ~500 files the graph response itself becomes an overhead burden that compounds
with every turn. The per-turn re-processing of a large tool response outweighs the
benefit of loading relevant files upfront.

---

## What Needs to Change for Large Repos

**Priority order:**

1. **Compress graph output (Issue 10)** — single biggest fix. Cut response from
   ~1,200 tokens to ~300 tokens. This reduces per-turn re-processing overhead by 75%.

2. **Lower Fix 3 inline threshold (Issue 11)** — at > 500 files, disable inline
   content. Return paths only. The N Read calls are cheaper than N × 35 turns of
   re-processing the inlined content.

3. **Cross-language edge detection** — add subprocess / API call detection to link
   Python endpoints to TypeScript callers. Without this, monorepo cross-stack queries
   will always need many manual turns to bridge the language gap.

4. **Routing awareness of repo size (extend Fix 1)** — for very large repos, tighten
   the routing so only clearly deep-tier queries reach the graph. Medium-tier queries
   that would have used the graph at 362 files should fall back to targeted grep at
   1,199 files.
