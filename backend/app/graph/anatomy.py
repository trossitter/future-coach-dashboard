"""KG1 anatomy hierarchy + clinical contraindication rules (hand-rolled,
aligned to OPE/SNOMED concepts — see docs/DESIGN-NOTES.md).

`part-of` lets an injury at one structure cascade correctly:
  - a region injury ("lower limb") reaches its child joints (knee/hip/ankle)
  - a sub-structure injury ("patellofemoral joint") rolls up to its joint (knee)
Safety traversal walks PART_OF in both directions along a structure's own path,
so sub-structures and containing regions count — but siblings do not.

`contraindicated-for` adds movement-pattern-level safety derived from the
injury's clinical notes (e.g. "avoid plyometrics") — this catches unsafe
exercises that don't happen to load the injured joint directly.
"""
from __future__ import annotations

from ..db import run

# joint -[:PART_OF]-> region
REGIONS = {
    "lower limb": ["knee", "hip", "ankle"],
    "upper limb": ["shoulder", "elbow", "wrist"],
    "spine": ["cervical spine", "thoracic spine", "lumbar spine"],
}

# sub-structure -[:PART_OF]-> joint
SUBSTRUCTURES = {
    "knee": ["patellofemoral joint", "tibiofemoral joint"],
}

# injury-note keyword(s) -> contraindicated movement patterns.
# Deep knee flexion is intentionally NOT a blanket squat ban: the joint-loading
# filter already excludes knee-loading work, and the member was explicitly
# cleared for low-impact (box) squats — so we only hard-contraindicate the
# unambiguous case (plyometrics). Documented trade-off.
NOTE_PATTERN_RULES = [
    (("plyometric", "plyo", "jumping", "high-impact", "high impact"),
     ["cardio - plyometric"]),
]


def build_anatomy() -> dict:
    for region, joints in REGIONS.items():
        run(
            """
            MERGE (r:Region {name: $region})
            WITH r UNWIND $joints AS jn
            MERGE (j:Joint {name: jn})
            MERGE (j)-[:PART_OF]->(r)
            """,
            region=region, joints=joints,
        )
    for joint, subs in SUBSTRUCTURES.items():
        run(
            """
            MERGE (j:Joint {name: $joint})
            WITH j UNWIND $subs AS sn
            MERGE (s:Joint {name: sn}) SET s.substructure = true
            MERGE (s)-[:PART_OF]->(j)
            """,
            joint=joint, subs=subs,
        )
    return {"regions": len(REGIONS),
            "substructures": sum(len(v) for v in SUBSTRUCTURES.values())}


def link_injury_contraindications() -> int:
    """Derive (:Injury)-[:CONTRAINDICATES]->(:MovementPattern) from injury notes."""
    n = 0
    for inj in run("MATCH (i:Injury) RETURN i.id AS id, coalesce(i.notes,'') AS notes"):
        notes = inj["notes"].lower()
        patterns = sorted({
            pat
            for keywords, pats in NOTE_PATTERN_RULES
            if any(k in notes for k in keywords)
            for pat in pats
        })
        if patterns:
            run(
                """
                MATCH (i:Injury {id: $id})
                UNWIND $patterns AS pn
                MERGE (p:MovementPattern {name: pn})
                MERGE (i)-[:CONTRAINDICATES]->(p)
                """,
                id=inj["id"], patterns=patterns,
            )
            n += len(patterns)
    return n
