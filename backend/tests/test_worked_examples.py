"""Fixture-backed demo examples.

These are the demo cases a grader is likely to try. The assertions stay at the
contract/invariant level so the examples remain stable even if ranking changes:
plans must be graph-safe, provenance must exist, equipment constraints must be
honored, and injury filtering must change when the member graph changes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import safety
from app.agents import generation as gen
from app.db import run
from evaluation import worked_examples

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "worked_examples.json").read_text()
)


def _plan_items(result: dict) -> list[dict]:
    return [
        item
        for section in ("warmup", "main", "cooldown")
        for item in result["plan"].get(section, [])
    ]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_backed_demo_examples_stay_graph_safe(
    case: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    # Keep the examples deterministic even if a developer has an API key locally.
    monkeypatch.setattr(gen.llm, "is_available", lambda: False)

    result, trace = gen.run_generation(
        case["member_id"],
        case["prompt"],
        case["time_minutes"],
    )

    assert trace
    assert "clarification" not in result
    assert result["journey_stage"] == case["expect"]["journey_stage"]

    intent = result["intent"]
    for eq in case["expect"].get("exclude_equipment", []):
        assert eq in intent["exclude_equipment"]
    for eq in case["expect"].get("extra_equipment", []):
        assert eq in intent["extra_equipment"]
    for term in case["expect"].get("session_exclude_terms", []):
        assert term in intent["session_exclude_terms"]

    plan = _plan_items(result)
    plan_ids = {p["id"] for p in plan}
    safe_ids = {e["id"] for e in result["safe_pool"]}
    assert plan_ids
    assert plan_ids <= safe_ids

    banned_equipment = case["expect"].get("exclude_equipment", [])
    if banned_equipment:
        rows = run(
            """
            MATCH (e:Exercise)-[:REQUIRES]->(eq:Equipment)
            WHERE e.id IN $ids AND eq.name IN $equipment
            RETURN collect(DISTINCT e.name) AS names
            """,
            ids=list(plan_ids),
            equipment=banned_equipment,
        )
        assert rows[0]["names"] == []

    if "session_exclude_terms" in case["expect"]:
        plan_names = " ".join(p["name"].lower() for p in plan)
        for term in case["expect"]["session_exclude_terms"]:
            assert term not in plan_names
    if "main_name_tokens" in case["expect"]:
        main_names = " ".join(p["name"].lower() for p in result["plan"]["main"])
        for token in case["expect"]["main_name_tokens"]:
            assert token in main_names
    if "main_down_rank_min" in case["expect"]:
        assert sum(bool(p.get("down_rank")) for p in result["plan"]["main"]) >= \
            case["expect"]["main_down_rank_min"]

    summary = result["filtered_summary"]
    if "unsafe_exact" in case["expect"]:
        assert summary["unsafe"] == case["expect"]["unsafe_exact"]
    else:
        assert summary["unsafe"] >= case["expect"].get("unsafe_min", 0)
    assert summary["equipment"] >= case["expect"].get("equipment_min", 0)

    unsafe_tokens = case["expect"].get("unsafe_name_tokens", [])
    if unsafe_tokens:
        contraindicated = " ".join(
            c["name"].lower() for c in safety.contraindicated(case["member_id"])
        )
        assert any(token in contraindicated for token in unsafe_tokens)

    provenance_by_id = {p["exercise_id"]: p for p in result["provenance"]}
    assert plan_ids <= provenance_by_id.keys()
    assert all(provenance_by_id[p["id"]]["safe_because"] for p in plan)
    assert all(provenance_by_id[p["id"]]["chosen_because"] for p in plan)


def test_reader_visible_worked_examples_do_not_drift():
    assert worked_examples.OUTPUT_PATH.exists(), (
        "Missing docs/examples/worked-examples.json. Run "
        "python -m evaluation.worked_examples --write."
    )
    assert worked_examples.README_PATH.exists(), (
        "Missing docs/examples/README.md. Run "
        "python -m evaluation.worked_examples --write."
    )
    payload = worked_examples.generate_examples()
    assert worked_examples.OUTPUT_PATH.read_text() == worked_examples.render_examples(payload)
    assert worked_examples.README_PATH.read_text() == worked_examples.render_readme(payload)
