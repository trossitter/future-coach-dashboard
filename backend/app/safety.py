"""Deterministic safety reasoning via graph traversal — NOT the LLM.

Three graph-derived constraints, all decided by walking edges:
  1. injury → joint, via the `part-of` hierarchy (sub-structures + regions count)
  2. injury → contraindicated movement pattern (e.g. plyometrics)
  3. equipment the member lacks
Every exclusion is explainable by returning the path that justified it, and the
equipment-alternatives query recovers feasible swaps when something is filtered.
"""
from __future__ import annotations

from .db import run

# Reusable WHERE fragments (static text, no user input). Each references `ex`
# (the candidate Exercise, bound in the outer query) and the `$id` param.
INJURY_JOINT_UNSAFE = """
EXISTS {
    MATCH (:Member {id: $id})-[:HAS_INJURY]->(:Injury)-[:AFFECTS]->(ij:Joint)
    MATCH (ex)-[:LOADS]->(loaded:Joint)
    WHERE (loaded)-[:PART_OF*0..]->(ij) OR (ij)-[:PART_OF*0..]->(loaded)
}
"""

INJURY_PATTERN_UNSAFE = """
EXISTS {
    MATCH (:Member {id: $id})-[:HAS_INJURY]->(:Injury)-[:CONTRAINDICATES]->(p:MovementPattern)
    MATCH (ex)-[:HAS_PATTERN]->(p)
}
"""

EQUIP_UNAVAILABLE = """
EXISTS {
    MATCH (ex)-[:REQUIRES]->(eq:Equipment)
    WHERE NOT (:Member {id: $id})-[:HAS_ACCESS_TO]->(eq)
}
"""

# Ad-hoc, this-session avoidance confirmed by the coach via the clarify loop.
# Same part-of traversal as the injury rule, but the joint set comes from the
# request ($avoid_joints) rather than the member's stored injuries. With an empty
# list the IN [] predicate is always false, so the whole EXISTS is harmless.
AVOID_JOINT_UNSAFE = """
EXISTS {
    MATCH (ex)-[:LOADS]->(loaded:Joint)
    MATCH (aj:Joint) WHERE aj.name IN $avoid_joints
      AND ((loaded)-[:PART_OF*0..]->(aj) OR (aj)-[:PART_OF*0..]->(loaded))
}
"""


def contraindicated(member_id: str) -> list[dict]:
    """Exercises unsafe by injury — joint (part-of aware) or movement pattern."""
    joint_rows = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_INJURY]->(inj:Injury)-[:AFFECTS]->(ij:Joint)
        MATCH (ex:Exercise)-[:LOADS]->(loaded:Joint)
        WHERE (loaded)-[:PART_OF*0..]->(ij) OR (ij)-[:PART_OF*0..]->(loaded)
        RETURN ex.id AS id, ex.name AS name,
               collect(DISTINCT loaded.name) AS via_joints,
               collect(DISTINCT inj.region) AS injuries
        """,
        id=member_id,
    )
    pattern_rows = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_INJURY]->(inj:Injury)
              -[:CONTRAINDICATES]->(p:MovementPattern)<-[:HAS_PATTERN]-(ex:Exercise)
        RETURN ex.id AS id, ex.name AS name,
               collect(DISTINCT p.name) AS via_patterns,
               collect(DISTINCT inj.region) AS injuries
        """,
        id=member_id,
    )
    merged: dict[str, dict] = {}
    for r in joint_rows:
        merged[r["id"]] = {"id": r["id"], "name": r["name"],
                           "injuries": r["injuries"],
                           "reasons": [{"type": "joint", "via": r["via_joints"]}]}
    for r in pattern_rows:
        m = merged.setdefault(r["id"], {"id": r["id"], "name": r["name"],
                                        "injuries": r["injuries"], "reasons": []})
        m["reasons"].append({"type": "pattern", "via": r["via_patterns"]})
    return sorted(merged.values(), key=lambda x: x["name"])


def eligible(member_id: str, *, muscle: str | None = None,
             pattern: str | None = None,
             exclude_terms: list[str] | None = None,
             avoid_joints: list[str] | None = None) -> list[dict]:
    """Exercises that are injury-safe, pattern-safe, and equipment-feasible —
    optionally narrowed to a muscle/pattern, minus excluded name terms, and minus
    any joints the coach confirmed to avoid this session (clarify loop)."""
    q = f"""
        MATCH (ex:Exercise)
        WHERE NOT {INJURY_JOINT_UNSAFE}
          AND NOT {INJURY_PATTERN_UNSAFE}
          AND NOT {EQUIP_UNAVAILABLE}
          AND NOT {AVOID_JOINT_UNSAFE}
          AND ($muscle IS NULL OR (ex)-[:TARGETS]->(:Muscle {{name: $muscle}}))
          AND ($pattern IS NULL OR (ex)-[:HAS_PATTERN]->(:MovementPattern {{name: $pattern}}))
          AND ($terms IS NULL OR NOT ANY(t IN $terms WHERE toLower(ex.name) CONTAINS t))
        RETURN ex.id AS id, ex.name AS name
        ORDER BY name
    """
    return run(q, id=member_id, muscle=muscle, pattern=pattern,
               terms=[t.lower() for t in exclude_terms] if exclude_terms else None,
               avoid_joints=avoid_joints or [])


def why_skipped(member_id: str, exercise_id: str) -> list[dict]:
    """Relationship path(s) making an exercise unsafe/infeasible. Empty => allowed."""
    joint = run(
        """
        MATCH (m:Member {id: $mid})-[:HAS_INJURY]->(inj:Injury)-[:AFFECTS]->(ij:Joint)
        MATCH (ex:Exercise {id: $exid})-[:LOADS]->(loaded:Joint)
        WHERE (loaded)-[:PART_OF*0..]->(ij) OR (ij)-[:PART_OF*0..]->(loaded)
        RETURN 'injury_joint' AS reason, inj.region AS detail,
               loaded.name AS via, ij.name AS injured_structure, ex.name AS exercise
        """,
        mid=member_id, exid=exercise_id,
    )
    pattern = run(
        """
        MATCH (m:Member {id: $mid})-[:HAS_INJURY]->(inj:Injury)
              -[:CONTRAINDICATES]->(p:MovementPattern)<-[:HAS_PATTERN]-(ex:Exercise {id: $exid})
        RETURN 'injury_pattern' AS reason, inj.region AS detail,
               p.name AS via, ex.name AS exercise
        """,
        mid=member_id, exid=exercise_id,
    )
    equip = run(
        """
        MATCH (ex:Exercise {id: $exid})-[:REQUIRES]->(eq:Equipment)
        WHERE NOT (:Member {id: $mid})-[:HAS_ACCESS_TO]->(eq)
        RETURN 'equipment' AS reason, eq.name AS detail, eq.name AS via, ex.name AS exercise
        """,
        mid=member_id, exid=exercise_id,
    )
    return joint + pattern + equip


def alternatives(member_id: str, exercise_id: str, limit: int = 5) -> list[dict]:
    """Safe, equipment-feasible swaps for a filtered exercise: same movement
    pattern or muscle, ranked by pattern overlap."""
    q = f"""
        MATCH (orig:Exercise {{id: $exid}})
        OPTIONAL MATCH (orig)-[:HAS_PATTERN]->(pp:MovementPattern)
        OPTIONAL MATCH (orig)-[:TARGETS]->(mm:Muscle)
        WITH orig, collect(DISTINCT pp) AS pats, collect(DISTINCT mm) AS muscles
        MATCH (ex:Exercise)
        WHERE ex.id <> orig.id
          AND (ANY(p IN pats WHERE (ex)-[:HAS_PATTERN]->(p))
               OR ANY(mu IN muscles WHERE (ex)-[:TARGETS]->(mu)))
          AND NOT {INJURY_JOINT_UNSAFE}
          AND NOT {INJURY_PATTERN_UNSAFE}
          AND NOT {EQUIP_UNAVAILABLE}
        WITH ex, size([p IN pats WHERE (ex)-[:HAS_PATTERN]->(p)]) AS pat_overlap
        RETURN ex.id AS id, ex.name AS name, pat_overlap
        ORDER BY pat_overlap DESC, name
        LIMIT $limit
    """
    return run(q, id=member_id, exid=exercise_id, limit=limit)
