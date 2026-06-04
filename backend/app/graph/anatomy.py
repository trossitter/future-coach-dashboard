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

# --- clinical contraindication rules -----------------------------------------
# Two layers, unioned per injury, both derived from the graph (never a prompt):
#
#   1. REGION_RULES — a systematic, region-keyed clinical default applied to
#      EVERY injury, so contraindication doesn't depend on whether free-text
#      notes happened to mention a pattern. Keyed by substring so "left knee" /
#      "right knee" both match "knee".
#   2. NOTE_RULES — note-specific overrides that catch member-specific detail the
#      region default can't ("avoid overhead", "loaded spinal flexion").
#
# This is a starter map aligned to OPE/SNOMED concepts and meant for
# clinician-in-the-loop review before production — the SHAPE is the deliverable;
# the depth grows under governance. Joint-loading (LOADS, part-of) already filters
# anything that stresses the injured joint, so these rules deliberately add only
# pattern-level risks that joint-loading misses (impact, overhead, loaded spine).
REGION_RULES = {
    "knee":     ["cardio - plyometric"],                       # impact through the knee
    "ankle":    ["cardio - plyometric"],                       # impact / landing
    "hip":      ["cardio - plyometric"],                       # impact through the hip
    "shoulder": ["upper push - vertical", "upper pull - vertical"],  # overhead loading
    "lower back": ["core - flexion", "core - extension", "core - rotation"],  # loaded spine
    "lumbar":   ["core - flexion", "core - extension", "core - rotation"],
    "cervical": ["upper push - vertical"],                     # overhead load through the neck
}

NOTE_RULES = [
    (("plyometric", "plyo", "jumping", "high-impact", "high impact"),
     ["cardio - plyometric"]),
    (("overhead",),
     ["upper push - vertical", "upper pull - vertical"]),
    (("spinal flexion", "lumbar flexion", "loaded spinal"),   # spine-specific only;
     ["core - flexion"]),                                      # "knee flexion" must NOT match
]


def _patterns_for(region: str, notes: str) -> list[str]:
    """Union of region-default + note-specific contraindicated patterns."""
    region_l, notes_l = (region or "").lower(), (notes or "").lower()
    pats: set[str] = set()
    for key, ps in REGION_RULES.items():
        if key in region_l:
            pats.update(ps)
    for keywords, ps in NOTE_RULES:
        if any(k in notes_l for k in keywords):
            pats.update(ps)
    return sorted(pats)


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
    """Derive (:Injury)-[:CONTRAINDICATES]->(:MovementPattern) from region default
    + note-specific clinical rules (see REGION_RULES / NOTE_RULES). These edges are
    fully derived, so rebuild rather than accumulate — a rule change must be able to
    REMOVE an edge, not only add one."""
    run("MATCH (:Injury)-[c:CONTRAINDICATES]->() DELETE c")
    n = 0
    for inj in run(
        "MATCH (i:Injury) RETURN i.id AS id, coalesce(i.region,'') AS region, "
        "coalesce(i.notes,'') AS notes"
    ):
        patterns = _patterns_for(inj["region"], inj["notes"])
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
