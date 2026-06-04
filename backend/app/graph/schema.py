"""Schema setup: uniqueness constraints + the Exercise vector index.

Node labels:  Exercise, Muscle, Joint, MovementPattern, Equipment,
              Member, Injury, Goal, Session, ChatSignal
Edge types:   LOADS, TARGETS, HAS_PATTERN, REQUIRES, PAIRS_WITH,
              HAS_INJURY, AFFECTS, HAS_ACCESS_TO, HAS_GOAL,
              PERFORMED, INCLUDED, SAID
See docs/SCHEMA.md for the full contract.
"""
from __future__ import annotations

from ..db import run

# bge-small-en-v1.5 emits 384-dim vectors; cosine is the right metric for it.
EMBED_DIM = 384

CONSTRAINTS = [
    "CREATE CONSTRAINT exercise_id IF NOT EXISTS FOR (e:Exercise) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT member_id IF NOT EXISTS FOR (m:Member) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT muscle_name IF NOT EXISTS FOR (n:Muscle) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT joint_name IF NOT EXISTS FOR (n:Joint) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT pattern_name IF NOT EXISTS FOR (n:MovementPattern) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT equipment_name IF NOT EXISTS FOR (n:Equipment) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT goal_name IF NOT EXISTS FOR (n:Goal) REQUIRE n.name IS UNIQUE",
]

VECTOR_INDEX = f"""
CREATE VECTOR INDEX exercise_embedding IF NOT EXISTS
FOR (e:Exercise) ON (e.embedding)
OPTIONS {{ indexConfig: {{
    `vector.dimensions`: {EMBED_DIM},
    `vector.similarity_function`: 'cosine'
}} }}
"""


def apply_schema() -> None:
    for stmt in CONSTRAINTS:
        run(stmt)
    run(VECTOR_INDEX)
