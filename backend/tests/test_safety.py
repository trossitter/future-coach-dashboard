"""Critical path #2 — safety from graph traversal.

Why this path: it is the platform's core promise. Safety must come from walking
the graph (injury → part-of joint, contraindicated movement pattern, equipment),
never from a prompt. The headline invariant is that NO contraindicated exercise
can ever be eligible — that's the test the whole architecture exists to pass.
"""
from app import safety
from app.db import run

JORDAN = "mbr_01HX9JORDAN"   # recovering left knee, no barbell


def test_eligible_is_disjoint_from_contraindicated():
    """The invariant: nothing contraindicated is ever eligible."""
    eligible = {e["id"] for e in safety.eligible(JORDAN)}
    contra = {c["id"] for c in safety.contraindicated(JORDAN)}
    assert eligible.isdisjoint(contra)


def test_knee_loading_exercises_are_contraindicated():
    names = {c["name"].lower() for c in safety.contraindicated(JORDAN)}
    assert any("lunge" in n or "squat" in n or "step" in n for n in names)


def test_eligible_respects_equipment():
    ids = [e["id"] for e in safety.eligible(JORDAN)]
    bad = run(
        "MATCH (e:Exercise)-[:REQUIRES]->(:Equipment {name:'Barbell'}) "
        "WHERE e.id IN $ids RETURN count(e) AS n", ids=ids)
    assert bad[0]["n"] == 0  # she has no barbell — none slip through


def test_pattern_contraindication_catches_non_joint_plyometrics():
    """A plyometric that doesn't load the knee is still excluded — by pattern,
    not joint. This is what joint-filtering alone would miss."""
    names = {c["name"].lower() for c in safety.contraindicated(JORDAN)}
    assert any("jump" in n for n in names)


def test_part_of_hierarchy_exists():
    r = run("MATCH (:Joint {name:'knee'})-[:PART_OF]->(:Region {name:'lower limb'}) "
            "RETURN count(*) AS n")
    assert r[0]["n"] == 1


def test_alternatives_are_themselves_safe():
    eligible = {e["id"] for e in safety.eligible(JORDAN)}
    contra = safety.contraindicated(JORDAN)
    checked = 0
    for c in contra:
        for a in safety.alternatives(JORDAN, c["id"]):
            assert a["id"] in eligible      # never suggest an unsafe alternative
            checked += 1
        if checked:
            break
    assert checked > 0


def test_why_skipped_explains_with_a_path():
    contra = safety.contraindicated(JORDAN)
    reasons = safety.why_skipped(JORDAN, contra[0]["id"])
    assert reasons and reasons[0]["reason"] in ("injury_joint", "injury_pattern", "equipment")
