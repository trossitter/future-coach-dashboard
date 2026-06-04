# Knowledge Graph Schema

Two graphs in one Neo4j labeled property graph, cross-linked. The graph does the
reasoning: safety is decided by traversal (not prompting), and every
recommendation is explainable by the path that produced it.

## KG1 — Movement / Clinical domain

| Label | Key | Notes |
|-------|-----|-------|
| `Exercise` | `id` | from `exercises.json`; carries `embedding` (vector index) |
| `Muscle` | `name` | 19 groups; SKOS-style `alt_labels` (gym jargon) |
| `Joint` | `name` | 9 joints + sub-structures (`substructure: true`); `alt_labels` |
| `Region` | `name` | anatomy hierarchy roots: `lower limb`, `upper limb`, `spine` |
| `MovementPattern` | `name` | 36 patterns, e.g. `lower push - squat` |
| `Equipment` | `name` | 32 types |

| Edge | From → To | Meaning |
|------|-----------|---------|
| `LOADS` | Exercise → Joint | joint stressed by the movement (**safety edge**) |
| `TARGETS` | Exercise → Muscle | trained muscle group |
| `HAS_PATTERN` | Exercise → MovementPattern | movement classification |
| `REQUIRES` | Exercise → Equipment | needed equipment |
| `PAIRS_WITH` | Exercise → Exercise | bilateral pair (`bilateral_pair_id`) |
| `PART_OF` | Joint → Joint/Region | anatomy hierarchy (sub-structure → joint → region) |
| `CONTRAINDICATES` | Injury → MovementPattern | clinical rule from injury notes (e.g. plyometrics) |

## KG2 — Member context (from `member-context.json` + synthetic extras)

| Label | Key | Notes |
|-------|-----|-------|
| `Member` | `id` | synthetic only; profile + preferences + scalar biomarkers as properties |
| `Goal` | `id` | `text`, `priority`, `target_date` |
| `Injury` | `id` | `region`, `joint`, `status`, `severity`, `since`, `notes`, `snomedct_hint` |
| `Session` | `member_id`+`date` | workout history; `completed`, `duration_min`, `rpe`, `exercises` |
| `AdherenceWeek` | `member_id`+`week_of` | `pct` — longitudinal adherence series |
| `WeightSample` | `member_id`+`date` | `kg` — longitudinal weight series |
| `Lab` (`:BloodPanel` / `:DexaScan`) | `member_id` | lab panels as properties |
| `ChatMessage` | `member_id`+`ts` | `from`, `text`, `embedding` (vector index), attachments |
| `CoachBrief` | `member_id`+`generated_for` | `churn_level`, `churn_reasons` |
| `MorningTask` | `member_id`+`type`+`text` | brief tasks (celebrate / review_risk / …) |

| Edge | From → To |
|------|-----------|
| `HAS_GOAL` | Member → Goal |
| `HAS_INJURY` | Member → Injury |
| `AFFECTS` | Injury → Joint | **cross-link into KG1** |
| `HAS_ACCESS_TO` | Member → Equipment | **cross-link into KG1** |
| `PERFORMED` | Member → Session |
| `HAS_ADHERENCE_WEEK` / `HAS_WEIGHT_SAMPLE` | Member → sample |
| `HAS_LAB` | Member → Lab |
| `SAID` | Member → ChatMessage |
| `HAS_BRIEF` / `HAS_TASK` | Member → CoachBrief → MorningTask |

The two graphs meet at `Injury -[:AFFECTS]-> Joint` and
`Member -[:HAS_ACCESS_TO]-> Equipment`: KG2 context drives KG1 safety traversal.

## The queries that matter (all in `app/safety.py`)

**Contraindicated** — injury via the anatomy hierarchy *or* a contraindicated pattern:
```
(Member)-[:HAS_INJURY]->(Injury)-[:AFFECTS]->(ij:Joint)
(Exercise)-[:LOADS]->(loaded:Joint)  WHERE (loaded)-[:PART_OF*0..]->(ij) OR (ij)-[:PART_OF*0..]->(loaded)
  -- OR --
(Member)-[:HAS_INJURY]->(Injury)-[:CONTRAINDICATES]->(:MovementPattern)<-[:HAS_PATTERN]-(Exercise)
```
`part-of` makes a region injury cascade to its joints and a sub-structure injury
roll up to its joint — siblings are not swept.

**Why?** the same paths returned for one exercise, plus an equipment branch.
**Alternatives** — same pattern/muscle, injury- and equipment-safe, ranked by pattern overlap.

## Notes on the data

- `priority_tier` is **constant (2) across all 50 exercises** — carried but it
  carries no ranking signal; documented rather than faked.
- Safe-pool sizes by member show the graph doing real work: Duncan 19 · Jordan 10
  · **Alia 3** (3 injuries + 2 equipment items → graceful degradation) · Paul 18.
