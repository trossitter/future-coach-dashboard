# Post-mortem: `preference_notes` ingested but never read

**Status:** Open · **Severity:** Low · **Date:** 2026-06-11

## Summary

Jordan Rivera's free-text preference notes ("Prefers dumbbell and kettlebell work; trains at home. Dislikes high-impact jumping.") were correctly ingested into the graph as `m.preference_notes` on the `Member` node. However, the generation agent, copilot agent, roster API endpoint, and frontend dashboard were all written without reading that field back — leaving it stranded in Neo4j. Workouts generated for Jordan may not honor the kettlebell/dumbbell preference or the high-impact dislike. No incorrect data is served; the preference is simply absent.

## Impact

- Jordan Rivera only: one member affected (the other three synthetic members have no preference notes).
- Generation surface: the LLM planner never receives the dumbbell/kettlebell preference or the high-impact dislike as context — it falls back on equipment access (which correctly lists Dumbbell and Kettlebell) but only by coincidence, not by stated intent.
- UI surface: the Generator component shows a "dislikes" chip strip but has no surface for free-text preference context.
- No safety impact. The hard `dislikes` array (`["Deadlift", "Burpees"]`) flows through correctly; only the softer free-text signal is missing.

## Timeline

| Time (Jun 4) | Event |
|---|---|
| ~14:08 | `28bac8c` — KG2 ingest written. Both `m.dislikes` (structured array) and `m.preference_notes` (free-text) written to the graph. |
| ~15:17 | `f0da95d` — Generation agent written. Queries only `m.dislikes`; `preference_notes` not included. |
| ~15:33 | `fcfdd8d` — Copilot agent written. Queries `m.preferred_session_minutes`; `preference_notes` not included. |
| ~15:46 | `b4eaa0a` — Frontend dashboard built. Roster Cypher returns `m.dislikes`; `preference_notes` not in query or response shape. |
| 2026-06-11 | Detected manually during a pre-deep-dive code review. |

## Root cause

The ingest and all consumers were written in a single rapid session (~70 minutes from KG2 commit to frontend commit). The `preference_notes` field was stored in the graph and implicitly deferred — it had no obvious consumer code shape. The structured `dislikes` array had a direct analog in the generation flow (`exclude_terms`), so it got wired up immediately; the free-text field did not.

**Evidence:** `grep -rn "preference_notes"` across the entire repo returns exactly two lines, both in `member_ingest.py` — write only, no reads anywhere.

## Contributing factors

1. **The data model splits preferences across two representations.** `dislikes` is a structured array; the dumbbell/kettlebell preference and high-impact dislike live only in `notes`. The structured field created a false sense that preferences were fully captured once wired up.

2. **The founding commit's verification statement didn't cover preferences.** The commit message reads: *"Verified live (Claude): Jordan at-risk → injury-safe bodyweight plan, 0 unsafe ids, provenance + narration."* Injury safety was spot-checked; preference alignment was not.

3. **No test asserted preference signal in generated output.** The worked-examples fixture and test suite (`test_worked_examples.py`) check that generation runs without error and returns exercises — not that the output reflects stated preferences.

4. **The roster endpoint was designed around triage signals.** Its Cypher query returns injuries, equipment, journey stage, and churn — the urgent signals a coach needs at a glance. `preference_notes` doesn't fit that profile, so it was reasonably excluded from the roster, and no separate endpoint was ever created for it.

## What went well

- The hard safety chain (injuries → contraindicated exercises) is completely unaffected. This is the architecture working as designed: safety is graph-traversal, not LLM context.
- The structured `dislikes` array does flow end-to-end correctly. The gap is confined to the softer free-text field.
- The field is stored in the graph with the correct name and value — the fix is purely additive (read it back, pass it through), no re-ingestion needed.

## Action items

- [x] Save this post-mortem to `docs/POSTMORTEM.md`.
- [x] **Thread `preference_notes` into the generation agent.** (`32d62aa`)
- [x] **Thread `preference_notes` into the roster endpoint and frontend.** (`80f6116`)
- [x] **Add a worked-example assertion for preference signal.** (`803c3ac`)
- [ ] **Out of scope (note once):** Normalizing `preferences.notes` into structured fields in the data model would eliminate the split representation — but that's a data-model change that touches ingest, schema, and tests. Not worth the churn for one field.
