# Handoff — synthesize Coach Sam's exercise library

**Status:** local experiment, not part of the submitted build (don't push).
**Goal:** generate a believable coach-authored exercise library — *rich enough to
feel like a real coach's repertoire, not exhaustive.* Target **~12–18 exercises**.

## Why this exists
A real coach has a signature style — favorite movements, their own cues, set/rep
schemes. Modeling the coach as a first-class entity (`(:Coach)-[:AUTHORED]->
(:CoachExercise)`) lets coach content flow through the *same* graph (and, later,
the same safety traversal) as the seed catalog. This handoff is about the **data**;
the UI (open from "Sam", save-from-member-plan) already exists in the experiment.

## Hard rules
- **Synthesize, don't scrape.** Author original exercises/cues. No real coach's
  named program, no copyrighted content — same principle as synthetic members,
  applied to content. Keep it clearly fictional ("Sam").
- **Mathematically/clinically sound.** Don't invent unsafe progressions or wrong
  rep/tempo prescriptions.

## Data shape (matches the live `POST /coach/library`)
```json
{
  "name": "Half-Kneeling Landmine Press",
  "pattern": "upper push - vertical",   // reuse a canonical MovementPattern name
  "sets": 3,
  "reps": "8-10 / side",
  "notes": "Shoulder-friendly vertical press for desk-bound members."  // the coach's cue
}
```
Already seeded (4): Tempo Goblet Squat, Half-Kneeling Landmine Press, Copenhagen
Plank Progression, RFE Split Squat. Add ~8–14 more.

## Coverage to aim for (this is the "rich enough" bar)
Spread across the catalog's movement-pattern families so the library reads as a
complete toolkit, not a theme:
- **lower**: squat, hinge, lunge (uni + bi-lateral)
- **upper push / pull**: horizontal + vertical
- **core**: anti-extension, anti-rotation, anti-lateral
- **carry / locomotion**, **conditioning / cardio**, **mobility / warmup**
Vary **equipment** (bodyweight, DB, KB, band, landmine, sled) and **rep scheme**
(strength 4–6, hypertrophy 8–12, tempo, time-based). Each `notes` is the coach's
*voice* — when/why they program it, a cue, a regression/progression.

## How to generate
Either hand-author, or prompt an LLM with: *"You are coach Sam. Write N original
signature exercises as JSON {name, pattern, sets, reps, notes}, covering [pattern
families above], varied equipment and rep schemes; notes = your coaching cue. Keep
it original — no real program names."* Then `POST` each to `/coach/library`, or add
a `data/coach-library.json` + a small seed loop in `main.py`'s lazy-seed.

## Plug deeper (optional, the real payoff)
To make coach exercises flow through safety like the catalog does, link each
`CoachExercise` to the graph: `-[:HAS_PATTERN]->(:MovementPattern)`,
`-[:LOADS]->(:Joint)`, `-[:REQUIRES]->(:Equipment)`. Then a coach-authored
exercise gets injury/equipment-filtered for free, and could be offered as a
candidate in the generator alongside the seed catalog.

## Acceptance
- 12–18 exercises, every pattern family represented, equipment + rep schemes varied.
- Each has a coach-voice `notes` line.
- Library opens from "Sam" and shows them; save-from-member-plan still works.
- Nothing scraped; all clearly fictional.
