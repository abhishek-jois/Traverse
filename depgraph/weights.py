"""Edge-weight scoring (1-10).

The weight expresses *how strongly* file A depends on file B — the thicker the
edge, the more likely a query touching A also needs B. Per the spec:

    8-10  heavily dependent — almost always need both files together
    4-7   moderately dependent — often relevant together
    1-3   loosely connected — rarely need to load both
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Relationship:
    """Aggregated dependency facts for a single (source -> target) pair."""

    symbol_count: int = 0          # distinct named symbols imported from target
    usage_total: int = 0           # how often those symbols are referenced
    has_inheritance: bool = False  # source extends a class from target
    is_reexport: bool = False      # source re-exports from target
    target_is_config: bool = False # target is a cross-cutting config file
    import_count: int = 0          # number of import statements A->B
    relations: set[str] = field(default_factory=set)


def compute_weight(rel: Relationship) -> int:
    """Map an aggregated :class:`Relationship` to an integer weight in [1, 10]."""
    score = 3.0  # an import edge exists at all

    # Inheritance is the strongest signal — you almost never read a subclass
    # without its base.
    if rel.has_inheritance:
        score += 4.0

    # Named imports are tighter than bare/side-effect imports.
    if rel.symbol_count >= 1:
        score += 1.0
    score += min(2.0, 0.5 * (rel.symbol_count - 1)) if rel.symbol_count > 1 else 0.0

    # Heavy use of the imported names means a tight runtime coupling.
    if rel.usage_total >= 1:
        # diminishing returns: 1 use -> +0.7, ~8 uses -> +2
        score += min(2.5, 0.7 + 0.3 * (rel.usage_total - 1))

    if rel.is_reexport:
        score += 1.0

    # Config dependencies are real but the *node* is always-included anyway,
    # so we keep their edges moderate rather than dominating the graph.
    if rel.target_is_config:
        score = min(score, 6.0)

    return max(1, min(10, round(score)))


def confidence_for(rel: Relationship, resolved_exactly: bool) -> str:
    """EXTRACTED for precise resolutions, INFERRED for fuzzy basename matches."""
    return "extracted" if resolved_exactly else "inferred"


def relation_labels(rel: Relationship) -> list[str]:
    labels: list[str] = []
    if rel.has_inheritance:
        labels.append("inherits")
    if rel.usage_total > 0:
        labels.append("calls")
    if rel.is_reexport:
        labels.append("reexports")
    if rel.target_is_config:
        labels.append("config")
    labels.append("imports")
    # stable, de-duplicated order
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        if lab not in seen:
            seen.add(lab)
            out.append(lab)
    return out
