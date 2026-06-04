"""Ingestion: turn exercises.json + members.json into graph nodes and edges.

Idempotent (MERGE everywhere) so re-running is safe. This is the pipeline the
spec asks for: raw JSON + unstructured signals -> structured nodes/edges.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..aliases import ALIASES
from ..config import settings
from ..db import run
from ..embeddings import embed
from .anatomy import build_anatomy, link_injury_contraindications
from .member_ingest import ingest_member_context
from .schema import apply_schema


def _load(name: str):
    return json.loads((Path(settings.data_dir) / name).read_text())


def _exists(name: str) -> bool:
    return (Path(settings.data_dir) / name).exists()


def ingest_exercises(exercises: list[dict]) -> int:
    """One round-trip per relationship type via UNWIND over the batch."""
    run(
        """
        UNWIND $rows AS ex
        MERGE (e:Exercise {id: ex.id})
        SET e.name = ex.name,
            e.priority_tier = ex.priority_tier,
            e.is_bilateral = ex.is_bilateral,
            e.bilateral_pair_id = ex.bilateral_pair_id,
            e.supports_weight = ex.supports_weight,
            e.is_reps = ex.is_reps,
            e.is_duration = ex.is_duration,
            e.estimated_rep_duration = ex.estimated_rep_duration
        WITH e, ex
        FOREACH (j IN ex.joints_loaded |
            MERGE (n:Joint {name: j}) MERGE (e)-[:LOADS]->(n))
        FOREACH (m IN ex.muscle_groups |
            MERGE (n:Muscle {name: m}) MERGE (e)-[:TARGETS]->(n))
        FOREACH (p IN ex.movement_patterns |
            MERGE (n:MovementPattern {name: p}) MERGE (e)-[:HAS_PATTERN]->(n))
        FOREACH (q IN ex.equipment_required |
            MERGE (n:Equipment {name: q}) MERGE (e)-[:REQUIRES]->(n))
        """,
        rows=exercises,
    )
    # Bilateral pairing — link both directions once both nodes exist.
    run(
        """
        UNWIND $rows AS ex
        WITH ex WHERE ex.bilateral_pair_id IS NOT NULL
        MATCH (a:Exercise {id: ex.id}), (b:Exercise {id: ex.bilateral_pair_id})
        MERGE (a)-[:PAIRS_WITH]->(b)
        """,
        rows=exercises,
    )
    return len(exercises)


def embed_exercises(exercises: list[dict]) -> int:
    """Populate the Exercise vector index. Embedding text fuses name + targeted
    muscles + movement pattern so semantic search ('pec isolation') lands on the
    right exercises even when the words never appear in the name."""
    texts = [
        f"{e['name']}. targets {', '.join(e['muscle_groups'])}. "
        f"movement: {', '.join(e['movement_patterns'])}."
        for e in exercises
    ]
    vecs = embed(texts)
    run(
        """
        UNWIND $rows AS r
        MATCH (e:Exercise {id: r.id})
        SET e.embedding = r.vec
        """,
        rows=[{"id": e["id"], "vec": v} for e, v in zip(exercises, vecs)],
    )
    return len(exercises)


def apply_aliases() -> int:
    """Attach gym-vocabulary altLabels to concept nodes (SKOS-style)."""
    n = 0
    for label, mapping in ALIASES.items():
        run(
            f"""
            UNWIND $rows AS r
            MATCH (n:{label} {{name: r.name}})
            SET n.alt_labels = r.alts
            """,
            rows=[{"name": k, "alts": v} for k, v in mapping.items()],
        )
        n += len(mapping)
    return n


def ingest_members() -> list[str]:
    """KG2: the provided rich member + any thin synthetic extras."""
    ids = [ingest_member_context(_load("member-context.json"))]
    if _exists("members-extra.json"):
        for em in _load("members-extra.json"):
            ids.append(ingest_member_context(em))
    return ids


def ingest_all() -> dict:
    apply_schema()
    exercises = _load("exercises.json")
    n_ex = ingest_exercises(exercises)
    n_embedded = embed_exercises(exercises)
    anatomy = build_anatomy()                 # KG1 part-of hierarchy (joints exist now)
    member_ids = ingest_members()             # KG2
    n_contra = link_injury_contraindications()  # injury -> movement-pattern edges
    n_alias = apply_aliases()
    return {"exercises": n_ex, "embedded": n_embedded,
            "members": len(member_ids), "member_ids": member_ids,
            "anatomy": anatomy, "injury_pattern_edges": n_contra,
            "aliased_concepts": n_alias}
