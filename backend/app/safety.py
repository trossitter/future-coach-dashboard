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

# Equipment feasibility, session-aware: an exercise is infeasible if it requires
# equipment the member can't use. Coach-added equipment ($extra_equipment) counts
# as available even without a stored edge; coach-excluded equipment
# ($exclude_equipment) counts as unavailable even if on file. With both lists
# empty this is just "requires equipment the member has no HAS_ACCESS_TO edge for",
# so callers without session context (e.g. alternatives()) pass [].
EQUIP_INFEASIBLE = """
EXISTS {
    MATCH (ex)-[:REQUIRES]->(eq:Equipment)
    WHERE (NOT (:Member {id: $id})-[:HAS_ACCESS_TO]->(eq)
           AND NOT eq.name IN $extra_equipment)
       OR eq.name IN $exclude_equipment
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
             avoid_joints: list[str] | None = None,
             exclude_equipment: list[str] | None = None,
             extra_equipment: list[str] | None = None) -> list[dict]:
    """Exercises that are injury-safe, pattern-safe, and equipment-feasible —
    optionally narrowed to a muscle/pattern, minus excluded name terms, minus
    any joints the coach confirmed to avoid this session (clarify loop), and with
    session equipment overrides ($extra_equipment available, $exclude_equipment
    unavailable)."""
    q = f"""
        MATCH (ex:Exercise)
        WHERE NOT {INJURY_JOINT_UNSAFE}
          AND NOT {INJURY_PATTERN_UNSAFE}
          AND NOT {EQUIP_INFEASIBLE}
          AND NOT {AVOID_JOINT_UNSAFE}
          AND ($muscle IS NULL OR (ex)-[:TARGETS]->(:Muscle {{name: $muscle}}))
          AND ($pattern IS NULL OR (ex)-[:HAS_PATTERN]->(:MovementPattern {{name: $pattern}}))
          AND ($terms IS NULL OR NOT ANY(t IN $terms WHERE toLower(ex.name) CONTAINS t))
        RETURN ex.id AS id, ex.name AS name
        ORDER BY name
    """
    return run(q, id=member_id, muscle=muscle, pattern=pattern,
               terms=[t.lower() for t in exclude_terms] if exclude_terms else None,
               avoid_joints=avoid_joints or [],
               exclude_equipment=exclude_equipment or [],
               extra_equipment=extra_equipment or [])


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


def alternatives(member_id: str, exercise_id: str, limit: int = 5, *,
                 avoid_joints: list[str] | None = None,
                 exclude_equipment: list[str] | None = None,
                 extra_equipment: list[str] | None = None) -> list[dict]:
    """Safe, feasible swaps for a filtered exercise: same movement pattern or
    muscle, ranked by pattern overlap. Honors the SAME session constraints as the
    plan (avoided joints + this-session equipment), so a suggested swap is never
    itself something the coach excluded — no "try instead: X" where X needs the
    very gear that was just removed."""
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
          AND NOT {EQUIP_INFEASIBLE}
          AND NOT {AVOID_JOINT_UNSAFE}
        WITH ex, size([p IN pats WHERE (ex)-[:HAS_PATTERN]->(p)]) AS pat_overlap
        RETURN ex.id AS id, ex.name AS name, pat_overlap
        ORDER BY pat_overlap DESC, name
        LIMIT $limit
    """
    return run(q, id=member_id, exid=exercise_id, limit=limit,
               avoid_joints=avoid_joints or [],
               exclude_equipment=exclude_equipment or [],
               extra_equipment=extra_equipment or [])


def equipment_filtered(member_id: str, *,
                       exclude_equipment: list[str] | None = None,
                       extra_equipment: list[str] | None = None) -> list[dict]:
    """Exercises removed for EQUIPMENT reasons only — injury-safe but missing (or
    coach-excluded) required equipment. Shaped to mirror contraindicated() so
    generation can merge the two filtered sets without de-duplicating."""
    q = f"""
        MATCH (ex:Exercise)
        WHERE NOT {INJURY_JOINT_UNSAFE}
          AND NOT {INJURY_PATTERN_UNSAFE}
          AND {EQUIP_INFEASIBLE}
        MATCH (ex)-[:REQUIRES]->(eq:Equipment)
        WHERE (NOT (:Member {{id: $id}})-[:HAS_ACCESS_TO]->(eq)
               AND NOT eq.name IN $extra_equipment)
           OR eq.name IN $exclude_equipment
        RETURN ex.id AS id, ex.name AS name,
               collect(DISTINCT eq.name) AS via
        ORDER BY name
    """
    rows = run(q, id=member_id,
               exclude_equipment=exclude_equipment or [],
               extra_equipment=extra_equipment or [])
    return [{"id": r["id"], "name": r["name"], "injuries": [],
             "reasons": [{"type": "equipment", "via": r["via"]}]} for r in rows]


def safety_reasons(member_id: str, exercise_id: str, *,
                   exclude_equipment: list[str] | None = None,
                   extra_equipment: list[str] | None = None) -> dict:
    """Derived per-exercise safety facts for the provenance layer to render.

    All checks are deterministic graph traversals reusing the exact part-of and
    contraindication semantics of the eligibility fragments. A false positive on
    any `*_ok` flag would be a safety regression, so each flag is computed from
    the same edges that drive filtering."""
    row = run(
        f"""
        MATCH (ex:Exercise {{id: $exid}})
        // injured_joints: directly-affected joints plus their part-of expansion
        // in both directions, matching the conflict test in INJURY_JOINT_UNSAFE.
        CALL () {{
            MATCH (:Member {{id: $id}})-[:HAS_INJURY]->(:Injury)-[:AFFECTS]->(ij:Joint)
            OPTIONAL MATCH (rel:Joint)
            WHERE (rel)-[:PART_OF*0..]->(ij) OR (ij)-[:PART_OF*0..]->(rel)
            RETURN collect(DISTINCT rel.name) AS injured_joints
        }}
        OPTIONAL MATCH (ex)-[:LOADS]->(j:Joint)
        OPTIONAL MATCH (ex)-[:HAS_PATTERN]->(p:MovementPattern)
        OPTIONAL MATCH (ex)-[:REQUIRES]->(eq:Equipment)
        RETURN
          collect(DISTINCT j.name)  AS joints_loaded,
          injured_joints,
          collect(DISTINCT p.name)  AS patterns,
          collect(DISTINCT eq.name) AS required_equipment,
          NOT {INJURY_JOINT_UNSAFE}    AS joint_ok,
          NOT {INJURY_PATTERN_UNSAFE}  AS pattern_ok,
          NOT {EQUIP_INFEASIBLE}       AS equipment_ok
        """,
        id=member_id, exid=exercise_id,
        exclude_equipment=exclude_equipment or [],
        extra_equipment=extra_equipment or [],
    )[0]
    return {
        "joints_loaded": row["joints_loaded"],
        "injured_joints": row["injured_joints"],
        "joint_ok": row["joint_ok"],
        "patterns": row["patterns"],
        "pattern_ok": row["pattern_ok"],
        "required_equipment": row["required_equipment"],
        "equipment_ok": row["equipment_ok"],
    }
