"""3-pass concept resolution: exact -> fuzzy -> embedding.

Maps a coach's (or member's) free text onto canonical graph nodes, with a
confidence score and graceful degradation: below the embedding threshold we
return the best guess flagged `needs_clarification` so the caller can ask
rather than silently mis-resolve.

  - exact     : normalized match against name + altLabels   (confidence 1.0)
  - fuzzy     : edit-distance / token ratio over surfaces   -> typos, jargon
  - embedding : semantic cosine over canonical labels        -> novel phrasing

Design choices that matter at scale / for safety:
  * Deterministic first. Exact + fuzzy over a curated alias table handle the
    common path with zero model variance; embeddings are the *fallback* for the
    long tail, never the backbone.
  * This resolver targets SMALL, BOUNDED vocabularies (muscles, joints,
    equipment, movement patterns) — these do not grow with the exercise count,
    so embedding all candidates once (cached) is cheap and stays cheap even
    with 50k exercises. Exercise lookup is a separate, index-backed path
    (`semantic_exercise_search`), so it scales sub-linearly via the Neo4j ANN
    index rather than re-embedding a growing set per call.
  * Label is validated against an allow-list: Cypher cannot parameterize a
    node label, so it is interpolated — the allow-list prevents injection.
"""
from __future__ import annotations

from functools import lru_cache

from rapidfuzz import fuzz, process

from .db import run
from .embeddings import cosine, embed, embed_query

FUZZY_ACCEPT = 85.0   # WRatio 0-100
EMBED_ACCEPT = 0.55   # cosine; below this we flag for clarification

# Only small, bounded concept vocabularies may be resolved here. Exercises are
# resolved via the vector index, not this in-memory path. The allow-list also
# closes the label-interpolation injection vector.
ALLOWED_LABELS = frozenset({"Muscle", "Joint", "Equipment", "MovementPattern"})


class UnknownLabel(ValueError):
    pass


def _surface_map(label: str) -> dict[str, str]:
    """surface form (lowercased) -> canonical name, including altLabels."""
    rows = run(
        f"MATCH (n:{label}) RETURN n.name AS name, coalesce(n.alt_labels, []) AS alts"
    )
    m: dict[str, str] = {}
    for r in rows:
        m[r["name"].lower()] = r["name"]
        for a in r["alts"]:
            m[a.lower()] = r["name"]
    return m


@lru_cache(maxsize=8)
def _concept_vectors(label: str) -> tuple[tuple[str, tuple[float, ...]], ...]:
    """Embed canonical names ONCE per label (cached). Bounded vocab => cheap."""
    canon = sorted({v for v in _surface_map(label).values()})
    return tuple((c, tuple(v)) for c, v in zip(canon, embed(canon)))


def resolve(text: str, label: str) -> dict:
    if label not in ALLOWED_LABELS:
        raise UnknownLabel(
            f"label must be one of {sorted(ALLOWED_LABELS)}; got {label!r}"
        )

    surfaces = _surface_map(label)
    if not surfaces:
        return _result(text, label, None, "none", 0.0, needs_clarification=True)

    norm = text.strip().lower()

    # 1) exact (name or alias)
    if norm in surfaces:
        return _result(text, label, surfaces[norm], "exact", 1.0)

    # 2) fuzzy over all surface forms (catches typos and jargon substrings)
    match_surface, score, _ = process.extractOne(
        norm, list(surfaces.keys()), scorer=fuzz.WRatio
    )
    if score >= FUZZY_ACCEPT:
        return _result(text, label, surfaces[match_surface], "fuzzy", score / 100.0)

    # 3) embedding fallback over canonical names (query gets the bge prefix)
    qv = embed_query(text)
    ranked = sorted(
        ((cosine(qv, list(vec)), name) for name, vec in _concept_vectors(label)),
        reverse=True,
    )
    best_score, best = ranked[0]
    return _result(
        text, label, best, "embedding", round(best_score, 3),
        needs_clarification=best_score < EMBED_ACCEPT,
        alternatives=[{"name": n, "score": round(s, 3)} for s, n in ranked[:3]],
    )


def _result(text, label, match, method, confidence, *,
            needs_clarification=False, alternatives=None) -> dict:
    return {
        "query": text,
        "label": label,
        "match": match,
        "method": method,
        "confidence": confidence,
        "needs_clarification": needs_clarification,
        "alternatives": alternatives or [],
    }


def semantic_exercise_search(text: str, k: int = 5) -> list[dict]:
    """Vector search over the Neo4j Exercise embedding index (the GraphRAG
    'semantic' half — graph traversal then narrows/safeties these). Scales
    sub-linearly via the ANN index, independent of dataset size."""
    qv = embed_query(text)
    return run(
        """
        CALL db.index.vector.queryNodes('exercise_embedding', $k, $vec)
        YIELD node, score
        RETURN node.id AS id, node.name AS name, round(score, 3) AS score
        ORDER BY score DESC
        """,
        k=k, vec=qv,
    )
