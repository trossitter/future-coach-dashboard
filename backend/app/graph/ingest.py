"""Ingestion: turn exercises.json + members.json into graph nodes and edges.

Idempotent (MERGE everywhere) so re-running is safe. This is the pipeline the
spec asks for: raw JSON + unstructured signals -> structured nodes/edges.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import settings
from ..db import run
from .schema import apply_schema


def _load(name: str) -> list[dict]:
    return json.loads((Path(settings.data_dir) / name).read_text())


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


def ingest_members(members: list[dict]) -> int:
    for m in members:
        run(
            """
            MERGE (mem:Member {id: $id})
            SET mem.name = $name,
                mem.experience_level = $experience_level
            WITH mem
            FOREACH (g IN $goals |
                MERGE (n:Goal {name: g}) MERGE (mem)-[:HAS_GOAL]->(n))
            FOREACH (eq IN $available_equipment |
                MERGE (n:Equipment {name: eq}) MERGE (mem)-[:HAS_ACCESS_TO]->(n))
            FOREACH (sig IN $chat_signals |
                CREATE (mem)-[:SAID]->(:ChatSignal {text: sig}))
            """,
            id=m["id"],
            name=m["name"],
            experience_level=m.get("experience_level", "unknown"),
            goals=m.get("goals", []),
            available_equipment=m.get("available_equipment", []),
            chat_signals=m.get("chat_signals", []),
        )
        # Injuries -> affected joints (the edge the safety filter traverses).
        for inj in m.get("injuries", []):
            run(
                """
                MATCH (mem:Member {id: $id})
                MERGE (i:Injury {name: $name})
                SET i.side = $side, i.severity = $severity, i.onset = $onset
                MERGE (mem)-[:HAS_INJURY]->(i)
                WITH i
                FOREACH (j IN $joints |
                    MERGE (n:Joint {name: j}) MERGE (i)-[:AFFECTS]->(n))
                """,
                id=m["id"],
                name=inj["name"],
                side=inj.get("side"),
                severity=inj.get("severity"),
                onset=inj.get("onset"),
                joints=inj.get("affects_joints", []),
            )
        # Workout history -> adherence / longitudinal signal.
        for s in m.get("sessions", []):
            run(
                """
                MATCH (mem:Member {id: $id})
                CREATE (mem)-[:PERFORMED]->(sess:Session {
                    date: $date, planned: $planned,
                    completed: $completed, adherence: $adherence})
                """,
                id=m["id"],
                date=s["date"],
                planned=s.get("planned"),
                completed=s.get("completed"),
                adherence=s.get("adherence"),
            )
    return len(members)


def ingest_all() -> dict:
    apply_schema()
    n_ex = ingest_exercises(_load("exercises.json"))
    n_mem = ingest_members(_load("members.json"))
    return {"exercises": n_ex, "members": n_mem}
