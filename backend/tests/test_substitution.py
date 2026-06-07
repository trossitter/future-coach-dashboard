"""Critical path — auto-substitution into the plan (PRD: "no barbell → drop
barbell-only exercises and find equivalent alternatives").

When an exercise that matches the coach's intent is dropped for missing/excluded
EQUIPMENT, the generator surfaces a SAFE same-pattern equivalent that actually
lands in the plan, tagged with `substitute_for`. Every substitute must come from
the graph-derived eligible set — safety is never the swap's escape hatch.

Jordan has a recovering left knee (so barbell lower-body lifts are filtered by
INJURY, not equipment) and no barbell. Her one injury-safe, barbell-requiring
exercise is the barbell decline bench press (chest / upper-push), so we use a
strength intent that includes chest to exercise the equipment-substitution path.
"""
from app import safety
from app.agents import generation as gen

JORDAN = "mbr_01HX9JORDAN"   # recovering left knee, no barbell
PROMPT = "full-body strength session, chest and arms"


def _intent_with_chest():
    """Intent as the planner would resolve it for the prompt — chest in scope so
    the barbell chest press (equipment-dropped) is a substitution candidate."""
    return {"target_muscles": ["chest", "triceps"], "target_patterns": [],
            "exclude_equipment": [], "extra_equipment": []}


def test_barbell_exercise_maps_to_a_non_barbell_safe_substitute():
    """At least one barbell-requiring, equipment-dropped exercise gets a safe,
    non-barbell substitute drawn from the eligible candidate pool."""
    candidates = safety.eligible(JORDAN)
    subs = gen._substitutions(JORDAN, _intent_with_chest(), candidates)
    assert subs, "expected at least one equipment substitution for Jordan"

    # a substitution for a barbell-requiring drop
    barbell = [s for s in subs if "barbell" in s["reason"].lower()
               or "barbell" in s["dropped"].lower()]
    assert barbell, "expected a barbell-requiring exercise to be substituted"
    s = barbell[0]
    assert "barbell" in s["reason"].lower()             # reason names the missing kit
    # the substitute itself must NOT require a barbell
    sub_req = safety.safety_reasons(JORDAN, s["substitute_id"])["required_equipment"]
    assert "Barbell" not in sub_req


def test_substitute_is_genuinely_safe():
    """The substitute is in the graph-derived eligible set — never an unsafe swap."""
    eligible = {e["id"] for e in safety.eligible(JORDAN)}
    candidates = safety.eligible(JORDAN)
    subs = gen._substitutions(JORDAN, _intent_with_chest(), candidates)
    assert subs
    for s in subs:
        assert s["substitute_id"] in eligible          # safety is non-negotiable
        assert s["substitute_id"] != s["dropped_id"]


def test_substitute_lands_in_the_plan_tagged():
    """End to end through the public crew: a substitute appears in the assembled
    plan with `substitute_for` set to the dropped exercise, and the result carries
    the `substitutions` list."""
    result, _trace = gen.run_generation(JORDAN, PROMPT, time_minutes=50)
    assert "clarification" not in result, "prompt should generate, not clarify"
    subs = result["substitutions"]
    assert subs, "expected substitutions in the result"

    plan = result["plan"]
    placed = [p for section in ("warmup", "main", "cooldown")
              for p in plan.get(section, [])]
    tagged = [p for p in placed if p.get("substitute_for")]
    assert tagged, "expected at least one prescribed item tagged substitute_for"

    # the tag must reference a real dropped exercise from the substitution list
    dropped_names = {s["dropped"] for s in subs}
    assert any(p["substitute_for"] in dropped_names for p in tagged)

    # and every placed substitute is itself safe (eligible)
    eligible = {e["id"] for e in safety.eligible(JORDAN)}
    for p in tagged:
        assert p["id"] in eligible


def test_no_substitution_without_intent_overlap():
    """A substitute is only offered for a drop that matches the coach's intent —
    an empty intent yields nothing (we don't swap exercises nobody asked for)."""
    candidates = safety.eligible(JORDAN)
    empty = {"target_muscles": [], "target_patterns": [],
             "exclude_equipment": [], "extra_equipment": []}
    assert gen._substitutions(JORDAN, empty, candidates) == []
