# Worked Example Captures

These examples are generated from `backend/tests/fixtures/worked_examples.json` by posting through the real `/generate` route with the deterministic no-key path.
Run `python -m evaluation.worked_examples --write` to refresh them; pytest checks both this summary and `worked-examples.json` for drift.

## jordan_injury_limited_equipment

Prompt: `Lower-body strength, protect the knee, no barbell, only dumbbells and a kettlebell, exclude deadlifts`

| What to inspect | Capture |
| --- | --- |
| Warmup | World's Greatest Stretch, Walking Toe Touches |
| Main | Alternating Dumbbell Racked Crossback Lunge, One-Kettlebell Hamstring Walkout, Bodyweight Pike, Dumbbell Neutral-Grip Bench Press |
| Cooldown | Ground Upper Trap Stretch, Cow Pose |
| Filter summary | 6 unsafe, 17 equipment, 5 shown |
| Graph proof | BOSU Step Over filtered: contraindicated pattern (cardio - plyometric)<br>Included carefully: World's Greatest Stretch, Alternating Dumbbell Racked Crossback Lunge, One-Kettlebell Hamstring Walkout |

## duncan_limited_equipment_no_injury

Prompt: `Full-body strength, only dumbbells and a kettlebell, no barbell, no machines`

| What to inspect | Capture |
| --- | --- |
| Warmup | Walking Toe Touches, Jump Rope - Single-Leg |
| Main | Dumbbell Neutral-Grip Bench Press, Alternating Dumbbell Overhead Press, Bench-Lying Single-Arm Dumbbell Tricep Extension, Dumbbell Incline Chest Fly |
| Cooldown | Standing Neck Circles, Bench-Supported Incline YTI |
| Filter summary | 0 unsafe, 33 equipment, 5 shown |
| Graph proof | Barbell Decline Bench Press filtered: requires Adjustable Bench - Decline, Barbell |

Full capture: [`worked-examples.json`](worked-examples.json).
