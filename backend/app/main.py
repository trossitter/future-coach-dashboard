"""FastAPI surface. Safety/retrieval endpoints are graph-backed; generation and
the copilot get wired in at later build steps.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import safety
from .db import run
from .graph.ingest import ingest_all

app = FastAPI(title="Future KG Coaching Platform", version="0.1.0")


@app.get("/health")
def health() -> dict:
    try:
        run("RETURN 1 AS ok")
        return {"status": "ok", "neo4j": "up"}
    except Exception as exc:  # surface connection issues plainly
        return {"status": "degraded", "neo4j": str(exc)}


@app.post("/ingest")
def ingest() -> dict:
    return ingest_all()


@app.get("/members/{member_id}/contraindicated")
def contraindicated(member_id: str) -> dict:
    rows = safety.contraindicated(member_id)
    return {"member_id": member_id, "count": len(rows), "exercises": rows}


@app.get("/members/{member_id}/eligible")
def eligible(member_id: str, muscle: str | None = None,
            pattern: str | None = None) -> dict:
    rows = safety.eligible(member_id, muscle=muscle, pattern=pattern)
    return {"member_id": member_id, "count": len(rows), "exercises": rows}


@app.get("/members/{member_id}/exercises/{exercise_id}/why")
def why(member_id: str, exercise_id: str) -> dict:
    rows = safety.why_skipped(member_id, exercise_id)
    return {
        "member_id": member_id,
        "exercise_id": exercise_id,
        "excluded": len(rows) > 0,
        "reasons": rows,
    }
