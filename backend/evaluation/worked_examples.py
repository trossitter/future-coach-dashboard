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
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

warnings.filterwarnings(
    "ignore",
    message=r"Using `httpx` with `starlette\.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from app.agents import generation as gen
from app.db import run
from app.graph.ingest import ingest_all
from app.main import app


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
README_PATH = OUTPUT_PATH.with_name("README.md")


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


def _request_for(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_id": case["member_id"],
        "prompt": case["prompt"],
        "time_minutes": case["time_minutes"],
    }


def _post_generate(client: TestClient, case: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    response = client.post("/generate", json=_request_for(case))
    response.raise_for_status()
    payload = response.json()
    return payload["result"], payload["trace"]


def _plan_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for section in ("warmup", "main", "cooldown")
        for item in result["plan"].get(section, [])
    ]


def _names_by_section(result: dict[str, Any]) -> dict[str, str]:
    return {
        section: ", ".join(item["name"] for item in result["plan"].get(section, [])) or "-"
        for section in ("warmup", "main", "cooldown")
    }


def _reason_text(reasons: list[dict[str, Any]]) -> str:
    chunks = []
    for reason in reasons:
        via = ", ".join(reason.get("via", []))
        if reason["type"] == "pattern":
            chunks.append(f"contraindicated pattern ({via})" if via else "contraindicated pattern")
        elif reason["type"] == "joint":
            chunks.append(f"loads {via}" if via else "loads injured joint")
        elif reason["type"] == "equipment":
            chunks.append(f"requires {via}" if via else "requires unavailable equipment")
    return "; ".join(chunks) or "-"


def _markdown_for(payload: dict[str, Any]) -> str:
    lines = [
        "# Worked Example Captures",
        "",
        (
            "These examples are generated from "
            "`backend/tests/fixtures/worked_examples.json` by posting through the "
            "real `/generate` route with the deterministic no-key path."
        ),
        (
            "Run `python -m evaluation.worked_examples --write` to refresh them; "
            "pytest checks both this summary and `worked-examples.json` for drift."
        ),
        "",
    ]
    for example in payload["examples"]:
        result = example["generated"]
        sections = _names_by_section(result)
        summary = result["filtered_summary"]
        down_ranked = [item["name"] for item in _plan_items(result) if item.get("down_rank")]
        pattern_filtered = next(
            (item for item in result.get("filtered_out", [])
             if any(r["type"] == "pattern" for r in item.get("reasons", []))),
            None,
        )
        equipment_filtered = next(
            (item for item in result.get("filtered_out", [])
             if any(r["type"] == "equipment" for r in item.get("reasons", []))),
            None,
        )
        proof = []
        if pattern_filtered:
            proof.append(f"{pattern_filtered['name']} filtered: {_reason_text(pattern_filtered['reasons'])}")
        if equipment_filtered:
            proof.append(f"{equipment_filtered['name']} filtered: {_reason_text(equipment_filtered['reasons'])}")
        if down_ranked:
            proof.append(f"Included carefully: {', '.join(down_ranked[:3])}")
        if not proof:
            proof.append("No injury contraindications active; equipment filtering still applies.")

        lines.extend([
            f"## {example['name']}",
            "",
            f"Prompt: `{example['request']['prompt']}`",
            "",
            "| What to inspect | Capture |",
            "| --- | --- |",
            f"| Warmup | {sections['warmup']} |",
            f"| Main | {sections['main']} |",
            f"| Cooldown | {sections['cooldown']} |",
            (
                "| Filter summary | "
                f"{summary.get('unsafe', 0)} unsafe, "
                f"{summary.get('equipment', 0)} equipment, "
                f"{summary.get('shown', 0)} shown |"
            ),
            f"| Graph proof | {'<br>'.join(proof)} |",
            "",
        ])
    lines.append("Full capture: [`worked-examples.json`](worked-examples.json).")
    lines.append("")
    return "\n".join(lines)


def generate_examples() -> dict[str, Any]:
    """Return the readable worked examples produced by the real API route."""
    _ensure_seeded()
    cases = json.loads(CASES_PATH.read_text())
    original_is_available: Callable[[], bool] = gen.llm.is_available
    gen.llm.is_available = lambda: False
    try:
        examples = []
        with TestClient(app) as client:
            for case in cases:
                result, trace = _post_generate(client, case)
                examples.append(
                    {
                        "name": case["name"],
                        "request": _request_for(case),
                        "fixture_expectations": case["expect"],
                        "generated": _project_result(result, trace),
                    }
                )
    finally:
        gen.llm.is_available = original_is_available

    return {
        "generated_by": "python -m evaluation.worked_examples --write",
        "route": "POST /generate",
        "source_fixture": "backend/tests/fixtures/worked_examples.json",
        "note": (
            "Readable capture of the deterministic no-key path. The pytest suite "
            "checks this file and README.md against current graph behavior so they "
            "cannot silently drift."
        ),
        "examples": examples,
    }


def render_examples(payload: dict[str, Any] | None = None) -> str:
    return json.dumps(payload or generate_examples(), indent=2) + "\n"


def render_readme(payload: dict[str, Any] | None = None) -> str:
    return _markdown_for(payload or generate_examples())


def write_examples() -> None:
    payload = generate_examples()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_examples(payload))
    README_PATH.write_text(render_readme(payload))
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Wrote {README_PATH}")


def check_examples() -> int:
    payload = generate_examples()
    expected = {
        OUTPUT_PATH: render_examples(payload),
        README_PATH: render_readme(payload),
    }
    stale = []
    for path, content in expected.items():
        if not path.exists() or path.read_text() != content:
            stale.append(path)
    if stale:
        for path in stale:
            print(f"{path} is stale or missing.")
        print("Run python -m evaluation.worked_examples --write and commit the update.")
        return 1
    print(f"{OUTPUT_PATH} and {README_PATH} are current.")
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
