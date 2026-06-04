# Knowledge Graph Schema

The graph is a **labeled property graph** (Neo4j). It does real work: safety is
decided by traversal (not prompting), and every recommendation is explainable by
the path that produced it.

## Nodes

| Label | Key | Notes |
|-------|-----|-------|
| `Exercise` | `id` | from `exercises.json`; carries `embedding` for vector search |
| `Muscle` | `name` | e.g. `chest`, `quads` |
| `Joint` | `name` | `knee`, `hip`, `shoulder`, `ankle`, `elbow`, `wrist`, `cervical/thoracic/lumbar spine` |
| `MovementPattern` | `name` | e.g. `lower push - squat` |
| `Equipment` | `name` | e.g. `Barbell`, `Dumbbell` |
| `Member` | `id` | synthetic; `do not use real member data` |
| `Injury` | `name` | `side`, `severity`, `onset` |
| `Goal` | `name` | member objectives |
| `Session` | — | workout history; `adherence` for longitudinal reasoning |
| `ChatSignal` | — | free-text; embedded for semantic concept resolution |

## Edges

| Edge | From → To | Meaning |
|------|-----------|---------|
| `LOADS` | Exercise → Joint | **the safety edge** — joint stressed by the movement |
| `TARGETS` | Exercise → Muscle | trained muscle group |
| `HAS_PATTERN` | Exercise → MovementPattern | movement classification |
| `REQUIRES` | Exercise → Equipment | needed equipment |
| `PAIRS_WITH` | Exercise → Exercise | bilateral pair (`bilateral_pair_id`) |
| `HAS_INJURY` | Member → Injury | |
| `AFFECTS` | Injury → Joint | injury → constrained joint |
| `HAS_ACCESS_TO` | Member → Equipment | available equipment |
| `HAS_GOAL` | Member → Goal | |
| `PERFORMED` | Member → Session | workout history |
| `INCLUDED` | Session → Exercise | what a session contained |
| `SAID` | Member → ChatSignal | unstructured signals |

## The two queries that matter

**Contraindicated (safety):**
```
(Member)-[:HAS_INJURY]->(Injury)-[:AFFECTS]->(Joint)<-[:LOADS]-(Exercise)
```
Any exercise reached by this walk loads an injured joint → excluded.

**Why? (explainability):** the same path, returned for a single exercise, plus an
equipment branch (`Exercise -[:REQUIRES]-> Equipment` the member lacks).

## Notes on the data

- `priority_tier` is **constant (2) across all 50 exercises** — carried as a
  property but it provides no ranking signal; documented rather than faked.
- A single `knee` injury contraindicates **21 of 50** exercises; the synthetic
  member's "no barbell" constraint removes more. Filtering is meaningful but
  still leaves a viable pool (~25), which is what makes the demo honest.
