"""Critical path — blanket "no equipment" routing + LLM-proposed/human-decided
equipment-context clarify.

Two distinct mechanisms are covered:

  1. The structured constraint. A generic "no equipment / bodyweight only" is a
     real intent (`bodyweight_only`) that drops EVERY equipment-requiring
     exercise — not an empty per-item exclusion that silently filters nothing.
     The graph stays the sole arbiter (test at the safety layer + end to end).

  2. The clarify gate. The LLM only decides WHETHER to ask about equipment and
     drafts the question ("traveling", "in a hotel" -> ambiguous); it never sets
     the constraint. The coach's answer re-enters as deterministic overrides.
     The LLM read is monkeypatched here so the gate's wiring is tested in
     isolation, with no network and no dependence on a key.
"""
import pytest

from app import safety
from app.agents import generation as gen
from app.db import run
from app.schemas import EquipmentContext

JORDAN = "mbr_01HX9JORDAN"   # recovering left knee, no barbell


def _requires_equipment(ids):
    """How many of these exercises have a REQUIRES->Equipment edge (graph truth)."""
    if not ids:
        return 0
    r = run(
        "MATCH (e:Exercise)-[:REQUIRES]->(:Equipment) "
        "WHERE e.id IN $ids RETURN count(DISTINCT e) AS n", ids=list(ids))
    return r[0]["n"]


def _plan_exercise_ids(result):
    plan = result["plan"]
    return [p["id"] for section in ("warmup", "main", "cooldown")
            for p in plan.get(section, [])]


# --- deterministic cue scan (the offline fallback) ---------------------------

def test_wants_bodyweight_only_matches_blanket_cues():
    """The phrase scan catches a blanket "no kit at all", the structured form a
    generic request resolves to when the LLM is unavailable."""
    for p in ("no equipment today", "bodyweight only please",
              "just bodyweight", "give her a calisthenics only session",
              "she has no gear with her"):
        assert gen._wants_bodyweight_only(p), p


def test_wants_bodyweight_only_ignores_named_kit_and_neutral_requests():
    """"no barbell" is a per-item exclusion (named kit), and "no weights" is a
    weighted-subset constraint that may still leave bands/mat — neither is the
    blanket bodyweight-only intent. A plain request matches nothing."""
    for p in ("no barbell, only dumbbells", "no weights today",
              "full body strength session", "easy on the knee"):
        assert not gen._wants_bodyweight_only(p), p


# --- structured constraint at the safety layer (graph truth) -----------------

def test_eligible_bodyweight_only_excludes_every_equipment_requiring_exercise():
    """`bodyweight_only=True` removes EVERY exercise with a REQUIRES->Equipment
    edge — a strict, non-empty subset of the normal eligible pool."""
    base = {e["id"] for e in safety.eligible(JORDAN)}
    bw = {e["id"] for e in safety.eligible(JORDAN, bodyweight_only=True)}
    assert bw, "expected at least one no-equipment exercise in the pool"
    assert bw < base, "bodyweight-only must be a strict subset of the full pool"
    assert _requires_equipment(bw) == 0, "no equipment-requiring exercise may slip through"


# --- clarify gate: LLM proposes, human decides -------------------------------

def test_gate_asks_equipment_context_on_ambiguous_situation(monkeypatch):
    """An unresolved equipment context ("traveling") -> the gate returns an
    `equipment_context` clarification carrying the LLM's drafted question, rather
    than silently assuming a full gym."""
    drafted = "Will she have any equipment while she's traveling, or just her body?"
    monkeypatch.setattr(gen, "detect_equipment_context", lambda prompt: EquipmentContext(
        situation="ambiguous", confidence=0.9, clarify_question=drafted))
    result, _trace = gen.run_generation(
        JORDAN, "put together a full-body session for her while she's traveling")
    clar = result.get("clarification")
    assert clar and clar["kind"] == "equipment_context"
    assert clar["questions"] == [drafted]


def test_gate_ignores_low_confidence_context(monkeypatch):
    """Below the confidence floor the gate does not ask — a low-confidence guess
    never injects a spurious clarification into the hot path."""
    monkeypatch.setattr(gen, "detect_equipment_context", lambda prompt: EquipmentContext(
        situation="ambiguous", confidence=0.2, clarify_question="what gear?"))
    result, _trace = gen.run_generation(JORDAN, "give her a full-body session")
    assert "clarification" not in result, "low-confidence context must not clarify"


def test_explicit_no_equipment_skips_llm_routing(monkeypatch):
    """An explicit "no equipment" short-circuits the LLM read entirely — there's
    nothing to ask, so detect_equipment_context is never called."""
    def _boom(prompt):
        raise AssertionError("LLM routing must be skipped on explicit phrasing")
    monkeypatch.setattr(gen, "detect_equipment_context", _boom)
    result, _trace = gen.run_generation(JORDAN, "bodyweight only workout, no equipment")
    assert "clarification" not in result, "explicit request should generate, not clarify"


def test_explicit_no_equipment_yields_a_bodyweight_only_plan():
    """End to end: "no equipment" produces a plan whose every exercise requires
    no equipment — the blanket constraint reaches the graph filter."""
    result, _trace = gen.run_generation(JORDAN, "give her a no-equipment full-body workout")
    assert "clarification" not in result, "explicit request should generate, not clarify"
    ids = _plan_exercise_ids(result)
    assert ids, "expected a non-empty plan"
    assert _requires_equipment(ids) == 0, "every prescribed exercise must need no equipment"
