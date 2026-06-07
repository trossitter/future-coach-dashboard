"""Generate reader-visible worked examples from the executable fixture.

The fixture in ``tests/fixtures/worked_examples.json`` is the source of truth.
This module turns those cases into a readable docs artifact, then checks that
the artifact still matches current deterministic behavior.

Run:
    python -m evaluation.worked_examples --write
    python -m evaluation.worked_examples --check
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.agents import generation as gen
from app.db import run
from app.graph.ingest import ingest_all


def _paths() -> tuple[Path, Path]:
    here = Path(__file__).resolve()
    candidates = [
        (
            Path("/app/tests/fixtures/worked_examples.json"),
            Path("/app/docs/examples/worked-examples.json"),
        ),
        (
            here.parents[1] / "tests" / "fixtures" / "worked_examples.json",
            here.parents[2] / "docs" / "examples" / "worked-examples.json",
        ),
    ]
    for fixture, output in candidates:
        if fixture.exists():
            return fixture, output
    return candidates[-1]


CASES_PATH, OUTPUT_PATH = _paths()


def _ensure_seeded() -> None:
    rows = run("MATCH (e:Exercise) RETURN count(e) AS n")
    if not rows or rows[0]["n"] == 0:
        ingest_all()


def _without_timing(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in event.items() if k != "ms"} for event in trace]


def _project_result(result: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "journey_stage": result["journey_stage"],
        "intent": result["intent"],
        "filtered_summary": result["filtered_summary"],
        "requested_unavailable": result.get("requested_unavailable", []),
        "substitutions": result.get("substitutions", []),
        "plan": result["plan"],
        "provenance": result["provenance"],
        "filtered_out": result.get("filtered_out", []),
        "audit_trace": _without_timing(trace),
    }


def generate_examples() -> dict[str, Any]:
    """Return the readable worked examples produced by current behavior."""
    _ensure_seeded()
    cases = json.loads(CASES_PATH.read_text())
    original_is_available: Callable[[], bool] = gen.llm.is_available
    gen.llm.is_available = lambda: False
    try:
        examples = []
        for case in cases:
            result, trace = gen.run_generation(
                case["member_id"],
                case["prompt"],
                case["time_minutes"],
            )
            examples.append(
                {
                    "name": case["name"],
                    "member_id": case["member_id"],
                    "prompt": case["prompt"],
                    "time_minutes": case["time_minutes"],
                    "fixture_expectations": case["expect"],
                    "generated": _project_result(result, trace),
                }
            )
    finally:
        gen.llm.is_available = original_is_available

    return {
        "generated_by": "python -m evaluation.worked_examples --write",
        "source_fixture": "backend/tests/fixtures/worked_examples.json",
        "note": (
            "Readable capture of the deterministic no-key path. The pytest suite "
            "checks this file against current graph behavior so it cannot silently drift."
        ),
        "examples": examples,
    }


def render_examples() -> str:
    return json.dumps(generate_examples(), indent=2) + "\n"


def write_examples() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_examples())
    print(f"Wrote {OUTPUT_PATH}")


def check_examples() -> int:
    expected = render_examples()
    if not OUTPUT_PATH.exists():
        print(f"Missing {OUTPUT_PATH}. Run python -m evaluation.worked_examples --write.")
        return 1
    actual = OUTPUT_PATH.read_text()
    if actual != expected:
        print(
            f"{OUTPUT_PATH} is stale. "
            "Run python -m evaluation.worked_examples --write and commit the update."
        )
        return 1
    print(f"{OUTPUT_PATH} is current.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate/check worked example docs.")
    parser.add_argument("--write", action="store_true", help="rewrite docs/examples artifact")
    parser.add_argument("--check", action="store_true", help="fail if docs artifact is stale")
    args = parser.parse_args()

    if args.write == args.check:
        parser.error("choose exactly one of --write or --check")

    if args.write:
        write_examples()
        return 0
    return check_examples()


if __name__ == "__main__":
    sys.exit(main())
