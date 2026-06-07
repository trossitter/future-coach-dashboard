"""Critical path — interactive adjustment driven by the graph (PRD examples).

The coach speaks in natural language; deterministic parsing routes each
constraint onto the graph (no LLM). These assert the three PRD acceptance
examples reach the graph correctly:

  1. "Exclude deadlifts"                  -> no deadlift variation appears
  2. "Her left knee is bothering her"     -> knee handled via the clarify gate
                                             + part-of hierarchy (test_safety.py)
  3. "no barbell, only dumbbells and ..." -> drop barbell, KEEP dumbbells/kettlebell
"""
from app import safety
from app.agents import generation as gen

JORDAN = "mbr_01HX9JORDAN"   # recovering left knee, no barbell

# A hand-built equipment surface so the polarity classifier is exercised in
# isolation — no DB. (surface form -> canonical Equipment name.)
EQUIP = {
    "barbell": "Barbell", "dumbbell": "Dumbbell", "dumbbells": "Dumbbell",
    "kettlebell": "Kettlebell", "band": "Band", "bands": "Band", "bench": "Bench",
}


# --- PRD ex.3: equipment polarity, clause-scoped -----------------------------

def test_only_clause_does_not_inherit_a_prior_no():
    """The bug this fixes: 'no barbell' must NOT bleed across the comma onto the
    kit named after 'only'."""
    exclude, extra = gen._classify_equipment(
        "she has no barbell, only dumbbells and a kettlebell", EQUIP)
    assert exclude == ["Barbell"]
    assert "Dumbbell" in extra and "Kettlebell" in extra
    assert "Dumbbell" not in exclude and "Kettlebell" not in exclude


def test_plain_no_still_excludes():
    exclude, extra = gen._classify_equipment("no bands today", EQUIP)
    assert exclude == ["Band"] and extra == []


def test_has_still_adds():
    exclude, extra = gen._classify_equipment("he has a bench", EQUIP)
    assert extra == ["Bench"] and exclude == []


def test_but_is_a_clause_boundary():
    """'has a bench but no barbell' — each kit takes its own clause's cue."""
    exclude, extra = gen._classify_equipment("has a bench but no barbell", EQUIP)
    assert exclude == ["Barbell"] and extra == ["Bench"]


# --- PRD ex.1: name-level exclusion parsing ----------------------------------

def test_exclude_by_name_parses_deadlift():
    known = {"knee", "shoulder", "barbell", "dumbbell", "chest", "core"}
    assert gen._resolve_exclude_terms("exclude deadlifts", known) == ["deadlift"]
    assert gen._resolve_exclude_terms("no more burpees please", known) == ["burpee"]
    assert gen._resolve_exclude_terms("skip the lunges", known) == ["lunge"]


def test_exclude_by_name_leaves_equipment_and_joints_to_their_resolvers():
    known = {"knee", "barbell"}
    assert gen._resolve_exclude_terms("no barbell", known) == []        # equipment path
    assert gen._resolve_exclude_terms("easy on the knee", known) == []  # no exclude cue


def test_depluralize():
    assert gen._depluralize("deadlifts") == "deadlift"
    assert gen._depluralize("burpees") == "burpee"
    assert gen._depluralize("press") == "press"   # -ss is not a plural


# --- integration: parsed terms actually filter through the graph -------------

def test_narration_payload_is_grounded_in_graph_reasons():
    """The narrator must receive the graph's actual filter reasons, not just names
    — otherwise it confabulates a rationale (the 'planks / knee flexion' bug)."""
    result = {
        "journey_stage": "at_risk",
        "plan": {"warmup": [], "main": [{"name": "Overhead Press"}], "cooldown": []},
        "filtered_out": [
            {"name": "Barbell Forward Lunge",
             "reasons": [{"type": "joint", "via": ["knee"]}]},
            {"name": "BOSU Step Over",
             "reasons": [{"type": "joint", "via": ["knee"]},
                         {"type": "pattern", "via": ["cardio - plyometric"]}]},
        ],
    }
    p = gen._narration_payload("full-body strength", result)
    fs = p["filtered_for_safety"]
    assert {f["name"] for f in fs} == {"Barbell Forward Lunge", "BOSU Step Over"}
    assert all(f["filtered_because"] for f in fs)            # every item is grounded
    lunge = next(f for f in fs if f["name"] == "Barbell Forward Lunge")
    assert lunge["filtered_because"] == ["loads the knee"]
    bosu = next(f for f in fs if f["name"] == "BOSU Step Over")
    assert "loads the knee" in bosu["filtered_because"]
    assert any("plyometric" in r for r in bosu["filtered_because"])


def test_exclude_term_removes_named_exercises_from_eligible():
    """End to end: an exclude term drops every eligible exercise whose name
    contains it — proving the parsed term reaches the graph's CONTAINS filter."""
    base = [e["name"] for e in safety.eligible(JORDAN)]
    # choose a real word from the eligible pool so the assertion is meaningful
    token = next((w for n in base for w in n.lower().split() if len(w) > 4), None)
    assert token, "expected at least one multi-letter word in the eligible pool"
    filtered = [e["name"] for e in safety.eligible(JORDAN, exclude_terms=[token])]
    assert all(token not in n.lower() for n in filtered)
    assert len(filtered) < len(base)
