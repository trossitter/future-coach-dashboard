"""FastAPI surface. Safety/retrieval endpoints are graph-backed; generation and
the copilot get wired in at later build steps.
"""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import llm, longitudinal, resolver, safety
from .agents import copilot
from .agents.generation import narration_stream, run_generation
from .db import run
from .graph.ingest import ingest_all
from .schemas import CopilotRequest, GenerateRequest

app = FastAPI(title="Future KG Coaching Platform", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


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


@app.get("/usage")
def usage() -> dict:
    """LLM token accounting + budget state (the graceful-degradation guard)."""
    from .config import settings
    return {
        "tokens_used": llm.tokens_used(),
        "token_budget": settings.llm_token_budget or None,
        "budget_exhausted": llm.budget_exhausted(),
        "llm_active": llm.is_available(),
    }


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


@app.get("/roster")
def roster() -> dict:
    """Coach overview: every member with the at-a-glance triage signals."""
    rows = run(
        """
        MATCH (m:Member)
        OPTIONAL MATCH (m)-[:HAS_INJURY]->(i:Injury)
        RETURN m.id AS id, m.name AS name, collect(DISTINCT i.region) AS injuries
        ORDER BY name
        """
    )
    out = []
    for r in rows:
        s = longitudinal.summary(r["id"])
        adh = s.get("adherence") or {}
        out.append({
            "id": r["id"], "name": r["name"],
            "injuries": [x for x in r["injuries"] if x],
            "journey_stage": s.get("journey_stage"),
            "adherence_pct": adh.get("latest_pct"),
            "adherence_trend": adh.get("trend"),
            "churn_level": s.get("churn_level"),
            "sleep_score": (s.get("oura") or {}).get("avg_sleep_score"),
        })
    return {"members": out}


@app.get("/members/{member_id}/longitudinal")
def member_longitudinal(member_id: str) -> dict:
    return longitudinal.summary(member_id)


@app.post("/generate")
def generate(req: GenerateRequest) -> dict:
    """Surface A: multi-agent workout generation with provenance + trace.
    Returns the structured plan fast; narration is streamed via /generate/stream."""
    result, trace = run_generation(
        req.member_id, req.prompt, req.time_minutes, req.exclude_terms,
        req.avoid_joints, req.ignore_joints
    )
    return {"result": result, "trace": trace}


@app.post("/generate/stream")
def generate_stream(req: GenerateRequest) -> StreamingResponse:
    """SSE: emit the structured plan + trace immediately, then stream narration."""
    def events():
        result, trace = run_generation(
            req.member_id, req.prompt, req.time_minutes, req.exclude_terms,
            req.avoid_joints, req.ignore_joints
        )
        yield f"event: result\ndata: {json.dumps({'result': result, 'trace': trace})}\n\n"
        # A clarification has no plan — skip narration, just close the stream.
        if not result.get("clarification"):
            for token in narration_stream(req.prompt, result):
                yield f"event: narration\ndata: {json.dumps(token)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/copilot")
def copilot_chat(req: CopilotRequest) -> StreamingResponse:
    """Surface B: route → retrieve KG2 slice → stream a grounded answer (SSE)."""
    def events():
        result, trace = copilot.run_copilot(req.member_id, req.question)
        yield f"event: context\ndata: {json.dumps({'result': result, 'trace': trace}, default=str)}\n\n"
        for token in copilot.answer_stream(req.question, result, req.history):
            yield f"event: answer\ndata: {json.dumps(token)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/members/{member_id}/chat")
def member_chat(member_id: str) -> dict:
    """PRD: the coach can see past chat history + images (attachment captions)."""
    msgs = copilot.chat_history(member_id)
    return {"member_id": member_id, "count": len(msgs), "messages": msgs}


@app.get("/members/{member_id}/brief")
def member_brief(member_id: str) -> dict:
    return copilot._retrieve_brief(member_id, "")


@app.get("/members/{member_id}/charts/{kind}")
def member_chart(member_id: str, kind: str) -> dict:
    return copilot.chart(member_id, kind)


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
