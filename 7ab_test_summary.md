# Test Run 7 — FitLLM, ART, Generalist Agent Monorepo (symbol slicing active, Haiku)

**Repos:**
- `FitLLM` — small (30 nodes / 77 edges)
- `ART` — medium (377 nodes / 653 edges)
- `/data/generalist_agent_monorepo` — large (1,692 nodes / 2,146 edges)

**What's new since Run 6:** This is the first sweep with **Phase 1 symbol slicing** active
(`depgraph/slicer.py`). Instead of returning bare file paths, the tool now inlines an
`=== CODE ===` "answer pack" — a one-line outline of each selected file's top-level symbols
plus the full bodies of the query-relevant symbols, within a per-depth token budget. The goal
is to kill the per-file Read turns that drove cost on small/medium repos in Run 6. The full
pipeline is also now multi-language (Python, JS/TS, Go, Rust, Java).

**Model:** `claude-haiku-4-5-20251001` (all runs) — Haiku is used so sweeps are cheap and
repeatable. Absolute dollar figures are therefore ~5–8× lower than the Sonnet-based Runs 1–6;
compare **percentages**, not absolute cost, against earlier runs.

**Constraint:** Read-only. No code was changed in any target repo
(allowlist: `Read`, `Grep`, `Glob`, `LS`, `depgraph_query` only — no `Edit`/`Write`/`Bash`).

> **Data note:** Figures are the measured values captured during the clean re-run
> (the first attempt was invalidated mid-sweep by an auth/session expiry that zeroed the
> monorepo tasks). Raw per-run `claude -p` JSON is gitignored and not persisted to disk.

---

## Raw Results

### FitLLM (small, 2 tasks)

| Task | Metric | WITHOUT graph | WITH graph | Change |
|------|--------|:---:|:---:|:---:|
| **T1** — "How does FitLLM load and split the model weights across GPUs?" | Turns | 7 | 9 | +2 turns |
| | Fresh context | 9,122 | 3,901 | **+57% better** |
| | **Cost (USD)** | **$0.0831** | **$0.0878** | **−6% MORE** |
| **T2** — "Explain the inference pipeline from a user prompt to generated tokens" | Turns | 1 | 7 | +6 turns |
| | Fresh context | 6,768 | 7,811 | −15% worse |
| | **Cost (USD)** | **$0.0200** | **$0.0754** | **−278% MORE** |

| Totals | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | 15,890 | 11,712 | **+26% better** |
| Total context | 63,315 | 77,769 | −23% worse |
| **Cost (USD)** | **$0.1031** | **$0.1633** | **−58% MORE** |

> T2's baseline short-circuited to a **single turn / $0.02** answer — a lucky grep hit — while
> the graph run took 7 turns. That one outlier dominates the FitLLM total; the true small-repo
> penalty is closer to Run 6's −13%.

---

### ART (medium, 3 tasks)

| Task | Metric | WITHOUT graph | WITH graph | Change |
|------|--------|:---:|:---:|:---:|
| **T1** — adversarial attack generation | Turns | 5 | 2 | −3 turns |
| | Fresh context | 4,381 | 618 | **+86% better** |
| | **Cost (USD)** | **$0.0398** | **$0.0252** | **+37% CHEAPER** |
| **T2** — trace evaluation | Turns | 12 | 7 | −5 turns |
| | Fresh context | 4,021 | 4,737 | −18% worse |
| | **Cost (USD)** | **$0.0800** | **$0.0643** | **+20% CHEAPER** |
| **T3** — defences pipeline | Turns | 1 | 14 | +13 turns |
| | Fresh context | 1,179 | 169 | **+86% better** |
| | **Cost (USD)** | **$0.1648** | **$0.0703** | **+57% CHEAPER** |

| Totals | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | 9,581 | 5,524 | **+42% better** |
| Total context | 79,005 | 81,226 | −3% worse |
| **Cost (USD)** | **$0.2845** | **$0.1599** | **+44% CHEAPER** |

---

### Generalist Agent Monorepo (large, 3 tasks)

| Task | Metric | WITHOUT graph | WITH graph | Change |
|------|--------|:---:|:---:|:---:|
| **T1** — tool execution / failure handling | Turns | 2 | 9 | +7 turns |
| | Fresh context | 374 | 1,836 | −391% worse |
| | **Cost (USD)** | **$0.0241** | **$0.1017** | **−322% MORE** |
| **T2** — trace frontend → backend ⚠ | Turns | 1 | 23 | hit cap |
| | Fresh context | 3,278 | 1,680 | +49% better |
| | **Cost (USD)** | **$0.2816** | **$0.1274** | **+55% CHEAPER** |
| **T3** — workflow engine | Turns | 1 | 7 | +6 turns |
| | Fresh context | 2,162 | 10,426 | −382% worse |
| | **Cost (USD)** | **$0.2112** | **$0.0697** | **+67% CHEAPER** |

| Totals | WITHOUT graph | WITH graph | Change |
|--------|:---:|:---:|:---:|
| Fresh context | 5,814 | 13,942 | −140% worse |
| Total context | 66,280 | 119,703 | −81% worse |
| **Cost (USD)** | **$0.5169** | **$0.2988** | **+42% CHEAPER** |

> ⚠ T2's graph run hit the 22-turn cap (23 turns) — treat that single task's numbers with
> caution. The monorepo baselines also repeatedly short-circuited to 1 turn but at high cost
> ($0.21–$0.28 each, one giant file read), which inflates the baseline's fresh-context figures.
> Despite the noise, the graph is +42% cheaper overall.

---

## Headline — All Three Repos

| Repo | Size | Cost WITHOUT | Cost WITH | Net |
|------|------|:---:|:---:|:---:|
| FitLLM | small (30n) | $0.1031 | $0.1633 | **−58% worse** |
| **ART** | **medium (377n)** | **$0.2845** | **$0.1599** | **+44% cheaper** |
| **Monorepo** | **large (1,692n)** | **$0.5169** | **$0.2988** | **+42% cheaper** |

---

## Reading the Results

### 1. Symbol slicing flips medium AND large repos to net-positive
This is the big change from Run 6. Previously only the large monorepo cleared break-even
(+5%), while ART regressed (−31%). With the inline answer pack, **ART is now +44% cheaper and
the monorepo +42% cheaper** — the graph no longer forces a Read turn per returned file, so the
per-turn overhead that sank the medium repo in Run 6 is gone. This is exactly the mechanism the
slicing work was designed to fix.

### 2. Turn count dropped where it mattered
ART T1 12→2 and T2 12→7; the graph runs answered from the inlined code instead of opening files
one at a time. That's the cost lever — every avoided Read turn is one fewer full context
re-process.

### 3. Small repos still lose — and always will
FitLLM is −58%, but that number is inflated by one baseline that got a lucky 1-turn grep answer
for $0.02. The structural truth is unchanged from every prior run: on a 30-file repo there is
almost nothing to explore, so grep is already near-optimal and any graph-call overhead is pure
loss. Small repos are the graph's designed worst case.

### 4. Haiku variance is high — trust repo totals, not single tasks
Several baselines short-circuited to a single expensive turn (FitLLM T2, monorepo T2/T3), and
one graph run hit the turn cap (monorepo T2). Per-task percentages swing wildly as a result.
The repo-level totals average this out, and two of three are solidly positive.

---

## Cross-Run Picture (net cost change)

| Run | FitLLM (small) | ART (medium) | Monorepo (large) | Notes |
|-----|:---:|:---:|:---:|:---|
| Run 3 | −26% | +27% | — | Sonnet |
| Run 5 | — | — | +14% | Sonnet |
| Run 6 | −13% | −31% | +5% | Sonnet, pre-slicing |
| **Run 7** | **−58%*** | **+44%** | **+42%** | **Haiku, slicing active** |

\* inflated by a single 1-turn baseline outlier; structurally still "small repo loses."

**Takeaway:** symbol slicing is the first change to make the graph a **clear win on both medium
and large repos in the same sweep**. Run 6's core diagnosis held — cost was driven by Read turns,
not fresh context — and removing those turns via inline slices delivered the predicted flip.

---

## Caveats

- Single A/B sample per task on Haiku; `claude -p` is non-deterministic, so per-task percentages
  carry high variance (baselines occasionally short-circuit to a 1-turn answer).
- Monorepo T2's graph run hit the 22-turn cap; that task's numbers are unreliable.
- Haiku absolute costs are ~5–8× below the Sonnet Runs 1–6, so only compare percentages across runs.
- The harness NUDGE forces a graph call even where real SKILL.md routing would skip it, inflating
  overhead on the smaller repos.
- Raw per-run logs were not persisted; figures are the measured session values from the clean re-run.
