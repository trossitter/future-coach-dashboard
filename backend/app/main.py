"""FastAPI surface. Safety/retrieval endpoints are graph-backed; generation and
the copilot get wired in at later build steps.
"""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from . import longitudinal, resolver, safety
from .agents.generation import narration_stream, run_generation
from .db import run
from .graph.ingest import ingest_all
from .schemas import GenerateRequest

app = FastAPI(title="Future KG Coaching Platform", version="0.1.0")


@app.on_event("startup")
def _warm_embedding_model() -> None:
    """Load the ONNX embedding model at boot so the first request isn't cold."""
    try:
        from .embeddings import embed
        embed(["warmup"])
    except Exception:
        pass


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


@app.get("/members")
def members() -> dict:
    rows = run(
        """
        MATCH (m:Member)
        OPTIONAL MATCH (m)-[:HAS_INJURY]->(i:Injury)
        RETURN m.id AS id, m.name AS name, m.tier AS tier,
               m.adherence_trend AS adherence_trend,
               collect(DISTINCT i.region) AS injuries
        ORDER BY name
        """
    )
    return {"count": len(rows), "members": rows}


@app.get("/members/{member_id}/longitudinal")
def member_longitudinal(member_id: str) -> dict:
    return longitudinal.summary(member_id)


@app.post("/generate")
def generate(req: GenerateRequest) -> dict:
    """Surface A: multi-agent workout generation with provenance + trace.
    Returns the structured plan fast; narration is streamed via /generate/stream."""
    result, trace = run_generation(
        req.member_id, req.prompt, req.time_minutes, req.exclude_terms
    )
    return {"result": result, "trace": trace}


@app.post("/generate/stream")
def generate_stream(req: GenerateRequest) -> StreamingResponse:
    """SSE: emit the structured plan + trace immediately, then stream narration."""
    def events():
        result, trace = run_generation(
            req.member_id, req.prompt, req.time_minutes, req.exclude_terms
        )
        yield f"event: result\ndata: {json.dumps({'result': result, 'trace': trace})}\n\n"
        for token in narration_stream(req.prompt, result):
            yield f"event: narration\ndata: {json.dumps(token)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


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


@app.get("/members/{member_id}/exercises/{exercise_id}/alternatives")
def alternatives(member_id: str, exercise_id: str, limit: int = 5) -> dict:
    rows = safety.alternatives(member_id, exercise_id, limit)
    return {"member_id": member_id, "exercise_id": exercise_id,
            "count": len(rows), "alternatives": rows}


@app.get("/resolve")
def resolve(text: str, label: str = "Muscle") -> dict:
    """3-pass concept resolution of free text onto a graph node label."""
    try:
        return resolver.resolve(text, label)
    except resolver.UnknownLabel as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/search/exercises")
def search_exercises(q: str, k: int = 5) -> dict:
    """Semantic (vector) search over the Exercise embedding index."""
    return {"query": q, "results": resolver.semantic_exercise_search(q, k)}
