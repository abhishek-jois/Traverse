"""Query traversal — the heart of Dependency Graph Retrieval.

Given a natural-language query, we:
  1. read only node *metadata* and score every file for relevance (cheap);
  2. seed from the best matches;
  3. propagate relevance along weighted edges, following thick edges and
     dropping below a cumulative-weight cutoff (the traversal-depth control);
  4. select the 2-5 best files and always-include any cross-cutting config;
  5. report how many tokens this saves versus loading the whole repo.
"""

from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field

import networkx as nx

# Tuning knobs.
DEFAULT_MAX_FILES = 5
SEED_LIMIT = 8                 # how many top matches seed the traversal
BASE_CUTOFF = 0.12             # stop propagating below this normalised relevance
MAX_CUTOFF = 0.28              # adaptive ceiling for very large repos
PROP_FACTOR_CAP = 0.95         # even weight-10 edges must decay, or strong chains never stop
FIELD_WEIGHTS = {"filename": 3.0, "symbols": 2.0, "description": 1.0, "type": 1.0}
CHARS_PER_TOKEN = 4          # rough token estimate from byte size

# Traversal *depth* presets. Each is (cutoff_multiplier, max_files, seed_limit):
#   - cutoff_multiplier scales the size-adaptive cutoff — lower means relevance
#     propagates further across the graph, so more distant files are reached;
#   - max_files caps how many files come back;
#   - seed_limit is how many top metadata matches seed the traversal.
# "balanced" is the default and deliberately reaches deeper than the original
# minimal 5-file selection, so more of the relevant neighbourhood is surfaced
# while still loading only a small fraction of the repo.
DEPTH_PRESETS: dict[str, tuple[float, int, int]] = {
    "focused":    (1.0, 5, 6),     # original tight behaviour
    "balanced":   (0.55, 8, 10),   # default — deeper reach, ~8 files
    "deep":       (0.30, 12, 14),
    "exhaustive": (0.15, 20, 20),
}
DEFAULT_DEPTH = "balanced"

# Grammatical / question words carry no signal about *which* file is relevant —
# without this filter, "what is gym" ranks files defining `is_updating_model`
# above files literally named gym_*.py.
STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has
have he her here him his how i if in into is it its may me might must my no not
of on or our shall she should so than that the their them then there these they
this those to us was we were what when where which who why will with would yes
you your please show find tell explain about
""".split())


def adaptive_cutoff(node_count: int) -> float:
    """Scale the traversal cutoff with repo size.

    In a large repo the graph is small-world — almost everything sits within a
    few hops of a god node — so the same cutoff that gives useful 2-3 hop
    reach in a 50-file project drags in noise at 5000 files. We tighten
    logarithmically: 0.12 up to ~200 files, ~0.19 at 800, capped at 0.28.
    """
    if node_count <= 200:
        return BASE_CUTOFF
    return min(MAX_CUTOFF, BASE_CUTOFF + 0.035 * math.log2(node_count / 200))


# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, splitting snake_case and camelCase."""
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9]+", text):
        # split camelCase / PascalCase into pieces, keep the whole too
        pieces = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", raw)
        out.append(raw.lower())
        out.extend(p.lower() for p in pieces if p)
    return out


def _node_fields(g: nx.DiGraph, n: str) -> dict[str, list[str]]:
    data = g.nodes[n]
    return {
        "filename": tokenize(n),
        "symbols": tokenize(" ".join(data.get("symbols", []))),
        "description": tokenize(data.get("description", "")),
        "type": tokenize(data.get("file_type", "")),
    }


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class Selected:
    path: str
    score: float
    reason: str
    file_type: str
    description: str
    tokens: int


@dataclass
class RetrievalResult:
    query: str
    selected: list[Selected] = field(default_factory=list)
    total_files: int = 0
    total_tokens: int = 0
    selected_tokens: int = 0
    metadata_tokens: int = 0   # cost of the cheap "graph entry" scan
    cutoff: float = BASE_CUTOFF  # traversal cutoff actually used

    @property
    def savings_pct(self) -> float:
        if self.total_tokens <= 0:
            return 0.0
        return 100.0 * (1 - self.selected_tokens / self.total_tokens)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _score_nodes(g: nx.DiGraph, query: str) -> dict[str, float]:
    """TF-IDF-ish relevance of each node to the query, metadata only."""
    all_terms = set(tokenize(query))
    q_terms = all_terms - STOPWORDS
    if not q_terms:           # query was entirely stopwords — better than nothing
        q_terms = all_terms
    if not q_terms:
        return {}

    docs = {n: _node_fields(g, n) for n in g.nodes}
    # document frequency per term (across the flattened doc of each node)
    df: dict[str, int] = {}
    for fields in docs.values():
        present = set()
        for toks in fields.values():
            present.update(toks)
        for t in present:
            df[t] = df.get(t, 0) + 1
    n_docs = max(1, len(docs))

    def idf(term: str) -> float:
        return math.log(1 + n_docs / (1 + df.get(term, 0)))

    scores: dict[str, float] = {}
    for n, fields in docs.items():
        s = 0.0
        for field_name, toks in fields.items():
            fw = FIELD_WEIGHTS[field_name]
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            for term in q_terms:
                if term in counts:
                    s += fw * counts[term] * idf(term)
                else:
                    # partial credit for substring matches in filenames
                    if field_name == "filename" and any(term in t for t in counts):
                        s += 0.4 * fw * idf(term)
        if s > 0:
            scores[n] = s
    return scores


def _propagate(g: nx.DiGraph, seeds: dict[str, float],
               cutoff: float) -> dict[str, tuple[float, str]]:
    """Spread normalised relevance along weighted edges (both directions).

    Edge factor = weight/10 (capped at :data:`PROP_FACTOR_CAP`), so thick edges
    carry relevance far and thin edges die quickly. Propagation stops below
    ``cutoff``. Returns ``node -> (relevance, reason)``.
    """
    best: dict[str, float] = dict(seeds)
    reason: dict[str, str] = {n: "direct metadata match" for n in seeds}
    # max-heap via negative relevance
    pq = [(-r, n) for n, r in seeds.items()]
    heapq.heapify(pq)

    while pq:
        neg, cur = heapq.heappop(pq)
        rel = -neg
        if rel < best.get(cur, 0.0):
            continue
        # consider neighbours in both directions
        for nbr, data, direction in _both_neighbors(g, cur):
            w = data.get("weight", 1)
            prop = rel * min(PROP_FACTOR_CAP, w / 10.0)
            if prop < cutoff:
                continue
            if prop > best.get(nbr, 0.0) + 1e-9:
                best[nbr] = prop
                rels = ",".join(data.get("relations", [])) or "imports"
                arrow = "→" if direction == "out" else "←"
                reason[nbr] = f"linked {arrow} {cur} (w={w}, {rels})"
                heapq.heappush(pq, (-prop, nbr))

    return {n: (best[n], reason[n]) for n in best}


def _both_neighbors(g: nx.DiGraph, n: str):
    for _, dst, data in g.out_edges(n, data=True):
        yield dst, data, "out"
    for src, _, data in g.in_edges(n, data=True):
        yield src, data, "in"


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def retrieve(g: nx.DiGraph, query: str, *,
             max_files: int | None = None,
             cutoff: float | None = None,
             depth: str = DEFAULT_DEPTH) -> RetrievalResult:
    """Run a query traversal.

    ``depth`` selects how far the traversal reaches and how many files come
    back (see :data:`DEPTH_PRESETS`). Explicit ``max_files`` / ``cutoff``
    override the preset; ``cutoff=None`` derives a size-adaptive threshold
    scaled by the depth multiplier.
    """
    mult, preset_max, seed_limit = DEPTH_PRESETS.get(
        depth, DEPTH_PRESETS[DEFAULT_DEPTH])
    if max_files is None:
        max_files = preset_max
    if cutoff is None:
        cutoff = adaptive_cutoff(g.number_of_nodes()) * mult
    result = RetrievalResult(query=query, total_files=g.number_of_nodes(),
                             cutoff=cutoff)

    # Token accounting.
    def node_tokens(n: str) -> int:
        return max(1, g.nodes[n].get("size", 0) // CHARS_PER_TOKEN)

    result.total_tokens = sum(node_tokens(n) for n in g.nodes)
    result.metadata_tokens = sum(
        max(1, (len(n) + len(g.nodes[n].get("description", ""))) // CHARS_PER_TOKEN)
        for n in g.nodes
    )

    raw = _score_nodes(g, query)
    if not raw:
        return result

    top = max(raw.values())
    norm = {n: s / top for n, s in raw.items()}
    seeds = dict(sorted(norm.items(), key=lambda kv: kv[1], reverse=True)[:seed_limit])

    spread = _propagate(g, seeds, cutoff)

    # Rank by propagated relevance, take the best `max_files`.
    ranked = sorted(spread.items(), key=lambda kv: kv[1][0], reverse=True)
    chosen: list[str] = [n for n, _ in ranked[:max_files]]

    # Always-include cross-cutting config that neighbours the chosen set —
    # capped so config fan-out can never dwarf the actual selection.
    chosen_set = set(chosen)
    extra = 0
    for n in list(chosen):
        for nbr, _, _ in _both_neighbors(g, n):
            if extra >= 3:
                break
            if (g.nodes[nbr].get("always_include")
                    and nbr not in chosen_set):
                chosen.append(nbr)
                chosen_set.add(nbr)
                extra += 1
                spread.setdefault(nbr, (0.0, "cross-cutting config (always included)"))

    for n in chosen:
        rel, why = spread.get(n, (0.0, ""))
        data = g.nodes[n]
        result.selected.append(Selected(
            path=n,
            score=round(rel, 3),
            reason=why,
            file_type=data.get("file_type", "other"),
            description=data.get("description", ""),
            tokens=node_tokens(n),
        ))

    result.selected_tokens = sum(s.tokens for s in result.selected)
    return result
