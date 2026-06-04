"""Deterministic safety reasoning via graph traversal — NOT the LLM.

This module is the heart of the platform's trust story: which exercises a
member may do is decided by walking edges, and every exclusion is explainable
by returning the exact path that justified it.
"""
from __future__ import annotations

from .db import run


def contraindicated(member_id: str) -> list[dict]:
    """Exercises that load a joint affected by one of the member's injuries.

    Path: (Member)-[:HAS_INJURY]->(Injury)-[:AFFECTS]->(Joint)<-[:LOADS]-(Exercise)
    """
    return run(
        """
        MATCH (m:Member {id: $id})-[:HAS_INJURY]->(inj:Injury)
              -[:AFFECTS]->(j:Joint)<-[:LOADS]-(ex:Exercise)
        RETURN ex.id   AS id,
               ex.name AS name,
               collect(DISTINCT j.name)   AS loaded_injured_joints,
               collect(DISTINCT inj.name) AS injuries
        ORDER BY name
        """,
        id=member_id,
    )


def unavailable_equipment(member_id: str) -> list[dict]:
    """Exercises requiring equipment the member has no access to."""
    return run(
        """
        MATCH (ex:Exercise)-[:REQUIRES]->(eq:Equipment)
        WHERE NOT (:Member {id: $id})-[:HAS_ACCESS_TO]->(eq)
        RETURN ex.id AS id, ex.name AS name,
               collect(DISTINCT eq.name) AS missing_equipment
        ORDER BY name
        """,
        id=member_id,
    )


def eligible(member_id: str, *, muscle: str | None = None,
             pattern: str | None = None) -> list[dict]:
    """Exercises that are BOTH injury-safe and equipment-feasible.

    Optionally narrowed to a target muscle or movement pattern. The two
    NOT EXISTS sub-queries are the deterministic safety gate.
    """
    return run(
        """
        MATCH (ex:Exercise)
        WHERE NOT EXISTS {
            MATCH (:Member {id: $id})-[:HAS_INJURY]->(:Injury)
                  -[:AFFECTS]->(j:Joint)<-[:LOADS]-(ex)
        }
        AND NOT EXISTS {
            MATCH (ex)-[:REQUIRES]->(eq:Equipment)
            WHERE NOT (:Member {id: $id})-[:HAS_ACCESS_TO]->(eq)
        }
        AND ($muscle  IS NULL OR (ex)-[:TARGETS]->(:Muscle {name: $muscle}))
        AND ($pattern IS NULL OR (ex)-[:HAS_PATTERN]->(:MovementPattern {name: $pattern}))
        RETURN ex.id AS id, ex.name AS name
        ORDER BY name
        """,
        id=member_id, muscle=muscle, pattern=pattern,
    )


def why_skipped(member_id: str, exercise_id: str) -> list[dict]:
    """The explainability query: return the relationship path(s) that make an
    exercise unsafe or infeasible for this member. Empty list => not excluded.
    """
    return run(
        """
        MATCH (:Member {id: $mid})-[:HAS_INJURY]->(inj:Injury)
              -[:AFFECTS]->(j:Joint)<-[:LOADS]-(ex:Exercise {id: $exid})
        RETURN 'injury'     AS reason, inj.name AS detail,
               j.name       AS via,    ex.name  AS exercise
        UNION
        MATCH (ex:Exercise {id: $exid})-[:REQUIRES]->(eq:Equipment)
        WHERE NOT (:Member {id: $mid})-[:HAS_ACCESS_TO]->(eq)
        RETURN 'equipment'  AS reason, eq.name AS detail,
               eq.name      AS via,    ex.name AS exercise
        """,
        mid=member_id, exid=exercise_id,
    )
