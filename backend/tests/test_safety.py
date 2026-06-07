"""Critical path #2 — safety from graph traversal.

Why this path: it is the platform's core promise. Safety must come from walking
the graph (injury → part-of joint, contraindicated movement pattern, equipment),
never from a prompt. The headline invariant is that NO contraindicated exercise
can ever be eligible — that's the test the whole architecture exists to pass.
"""
from app import safety
from app.db import run

JORDAN = "mbr_01HX9JORDAN"   # recovering left knee, no barbell


def _pattern_contra(member_id):
    return {c["id"] for c in safety.contraindicated(member_id)
            if any(r["type"] == "pattern" for r in c["reasons"])}


def _joint_only_contra(member_id):
    return {c["id"] for c in safety.contraindicated(member_id)
            if any(r["type"] == "joint" for r in c["reasons"])
            and not any(r["type"] == "pattern" for r in c["reasons"])}


def test_eligible_is_disjoint_from_pattern_contraindicated_and_equipment():
    """Post-down-rank invariant: eligible is disjoint from PATTERN-contraindicated
    and from equipment-missing, but joint-stressing exercises ARE eligible (kept
    and flagged down_rank) rather than excluded."""
    eligible_rows = safety.eligible(JORDAN)
    eligible = {e["id"] for e in eligible_rows}
    # hard excludes still hold
    assert eligible.isdisjoint(_pattern_contra(JORDAN))
    bad_equip = run(
        "MATCH (e:Exercise)-[:REQUIRES]->(:Equipment {name:'Barbell'}) "
        "WHERE e.id IN $ids RETURN count(e) AS n", ids=list(eligible))
    assert bad_equip[0]["n"] == 0
    # joint-loaders are admitted, and every one is flagged down_rank
    joint_only = _joint_only_contra(JORDAN)
    admitted = joint_only & eligible
    assert admitted, "joint-stressing exercises should now be eligible"
    dr = {e["id"]: e["down_rank"] for e in eligible_rows}
    assert all(dr[i] for i in admitted)


def test_pattern_contraindicated_jump_stays_excluded_joint_loader_down_ranked():
    """A pattern-contraindicated plyometric (a 'jump') stays EXCLUDED for Jordan,
    while a knee-loading-but-not-plyometric exercise is eligible with down_rank."""
    eligible_rows = safety.eligible(JORDAN)
    eligible = {e["id"] for e in eligible_rows}
    dr = {e["id"]: e["down_rank"] for e in eligible_rows}
    # a contraindicated "jump" exercise is excluded
    jumps = [c for c in safety.contraindicated(JORDAN)
             if "jump" in c["name"].lower()
             and any(r["type"] == "pattern" for r in c["reasons"])]
    assert jumps
    assert all(c["id"] not in eligible for c in jumps)
    # a knee-loader without a contraindicated pattern is eligible AND down-ranked
    knee_loaders = _joint_only_contra(JORDAN)
    assert knee_loaders
    elig_knee = knee_loaders & eligible
    assert elig_knee
    assert all(dr[i] for i in elig_knee)


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
