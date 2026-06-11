"""FastAPI surface. Safety/retrieval endpoints are graph-backed; generation and
the copilot get wired in at later build steps.
"""
from __future__ import annotations

import json

from fastapi import Body, FastAPI, HTTPException
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
def _bootstrap() -> None:
    """Warm the embedding model and seed the graph on first boot, so a single
    `docker compose up` is genuinely all it takes — no manual /ingest call.

    Ingest is idempotent (MERGE) and skipped when the graph is already populated,
    so `--reload` restarts stay fast; a connection blip never crashes boot."""
    try:
        from .embeddings import embed
        embed(["warmup"])
    except Exception:
        pass
    try:
        rows = run("MATCH (e:Exercise) RETURN count(e) AS n")
        if not rows or rows[0]["n"] == 0:
            ingest_all()
    except Exception:
        pass  # neo4j not ready yet / transient — first request can still /ingest


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


@app.get("/roster")
def roster() -> dict:
    """Coach overview: every member with the at-a-glance triage signals."""
    rows = run(
        """
        MATCH (m:Member)
        OPTIONAL MATCH (m)-[:HAS_INJURY]->(i:Injury)
        OPTIONAL MATCH (m)-[:HAS_ACCESS_TO]->(eq:Equipment)
        RETURN m.id AS id, m.name AS name, collect(DISTINCT i.region) AS injuries,
               collect(DISTINCT eq.name) AS equipment,
               coalesce(m.dislikes, []) AS dislikes,
               m.preference_notes AS preference_notes
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
            "equipment": sorted(x for x in r["equipment"] if x),
            "dislikes": [x for x in r["dislikes"] if x],
            "preference_notes": r.get("preference_notes") or "",
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
        req.avoid_joints, req.ignore_joints,
        req.exclude_equipment, req.extra_equipment
    )
    return {"result": result, "trace": trace}


@app.post("/generate/stream")
def generate_stream(req: GenerateRequest) -> StreamingResponse:
    """SSE: emit the structured plan + trace immediately, then stream narration."""
    def events():
        result, trace = run_generation(
            req.member_id, req.prompt, req.time_minutes, req.exclude_terms,
            req.avoid_joints, req.ignore_joints,
            req.exclude_equipment, req.extra_equipment
        )
        yield f"event: result\ndata: {json.dumps({'result': result, 'trace': trace})}\n\n"
        # A clarification has no plan — skip narration, just close the stream.
        if not result.get("clarification"):
            for token in narration_stream(req.prompt, result):
                yield f"event: narration\ndata: {json.dumps(token)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/members/{member_id}/deliver")
def deliver(member_id: str, body: dict = Body(default={})) -> dict:
    """On-platform handoff: record the generated plan to the member's record so it
    lands in their app — the platform-native alternative to exporting or printing."""
    ids = body.get("exercise_ids") or []
    run(
        """
        MATCH (m:Member {id: $mid})
        CREATE (m)-[:RECEIVED_PLAN]->(:DeliveredPlan {
            created: datetime(), exercise_count: $n, summary: $summary,
            message: $message})
        """,
        mid=member_id, n=len(ids), summary=(body.get("summary") or "")[:200],
        # the coach-authored (possibly hand-edited) note that rides to the member
        message=(body.get("message") or "")[:1000],
    )
    return {"delivered": True, "exercise_count": len(ids)}


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


# --- coach library (LOCAL EXPERIMENT — not part of the submitted build) -------
# Coach Sam's own exercises as first-class graph nodes (Coach)-[:AUTHORED]->
# (CoachExercise), so coach-authored content could flow through the same system.
_COACH_ID = "sam"
_COACH_SEED = [
    {"name": "Sam's Tempo Goblet Squat", "pattern": "lower push - squat",
     "sets": 4, "reps": "6 @ 3-1-1 tempo",
     "notes": "Default lower-body strength builder — owns the eccentric."},
    {"name": "Half-Kneeling Landmine Press", "pattern": "upper push - vertical",
     "sets": 3, "reps": "8-10 / side",
     "notes": "Shoulder-friendly vertical press for desk-bound members."},
    {"name": "Copenhagen Plank Progression", "pattern": "core - anti-lateral flexion",
     "sets": 3, "reps": "20-30s / side", "notes": "Adductor + lateral-core staple for runners."},
    {"name": "Rear-Foot-Elevated Split Squat", "pattern": "lower push - split squat",
     "sets": 3, "reps": "8 / side", "notes": "Unilateral strength once a knee is cleared."},
    {"name": "Tall-Kneeling Band Pulldown", "pattern": "upper pull - vertical",
     "sets": 3, "reps": "10-12",
     "notes": "Keeps ribs stacked while teaching a clean vertical pull without a machine."},
    {"name": "Chest-Supported Dumbbell Row Ladder", "pattern": "upper pull - horizontal",
     "sets": 4, "reps": "6-8-10-12",
     "notes": "My back-builder when I want hard pulling without loading the low back."},
    {"name": "Feet-Elevated Tempo Push-Up", "pattern": "upper push - horizontal",
     "sets": 3, "reps": "6-10 @ 3-sec lower",
     "notes": "Progress before heavier pressing; pause with the chest one inch from the floor."},
    {"name": "Kickstand Kettlebell RDL", "pattern": "lower pull - hip lift",
     "sets": 3, "reps": "8 / side",
     "notes": "Hinge practice with just enough asymmetry to expose side-to-side control."},
    {"name": "Reverse Lunge to Low Box", "pattern": "lower push - lunge",
     "sets": 3, "reps": "8 / side",
     "notes": "My go-to lunge regression: tap the box, stay tall, drive through the front foot."},
    {"name": "Sled Push Breathing Intervals", "pattern": "cardio - locomotion",
     "sets": 6, "reps": "20m push + 60s easy walk",
     "notes": "Conditioning that feels athletic without asking for a complicated skill."},
    {"name": "Suitcase Carry Reset", "pattern": "core - carry",
     "sets": 4, "reps": "30-40m / side",
     "notes": "Walk slowly enough that the weight can't pull you into a side bend."},
    {"name": "Half-Kneeling Pallof Press Reach", "pattern": "core - anti-rotation",
     "sets": 3, "reps": "8-10 / side",
     "notes": "Anti-rotation with a reach so the member earns the exhale and rib position."},
    {"name": "Dead-Bug Heel Tap Series", "pattern": "core - anti-extension",
     "sets": 3, "reps": "6-8 / side",
     "notes": "Use this when bracing is the limiter; low back stays heavy on the floor."},
    {"name": "Lateral Band Walk Primer", "pattern": "lower - abduction",
     "sets": 2, "reps": "10 steps each way",
     "notes": "Warm hips before squats or carries; quiet upper body, steady foot pressure."},
    {"name": "World's-Greatest Stretch Flow", "pattern": "mobility - dynamic",
     "sets": 2, "reps": "4 / side",
     "notes": "A fast warmup flow when I need hips, t-spine, and hamstrings online."},
    {"name": "90-90 Breathing Cooldown", "pattern": "regen",
     "sets": 2, "reps": "5 slow breaths",
     "notes": "Downshift after hard sessions; heels on the wall, exhale until ribs drop."},
]


def _clib_id(name: str) -> str:
    return "clib_" + "".join(ch for ch in name.lower() if ch.isalnum())[:28]


def _region_for(pattern: str | None, name: str | None) -> str:
    """Map an exercise to a body region a coach browses by — target-first, since
    a coach thinks 'chest today', not 'find the name starting with F'."""
    p, n = (pattern or "").lower(), (name or "").lower()
    if any(t in n for t in ("tricep", "bicep", "curl")):
        return "Arms"
    if "upper push" in p:
        return "Shoulders" if "vertical" in p else "Chest"
    if "upper pull" in p or "row" in n or "pull-up" in n:
        return "Back"
    if "core" in p or "plank" in n or "ab " in n:
        return "Core"
    if "hinge" in p or "deadlift" in n or "glute" in n or "hip thrust" in n:
        return "Glutes"
    if "lower" in p or "squat" in n or "lunge" in n:
        return "Legs"
    if any(t in p for t in ("cardio", "plyo", "condition")):
        return "Cardio"
    return "Full body"


def _coach_library_upsert(body: dict) -> None:
    name = (body.get("name") or "").strip()
    if not name:
        return
    run(
        """
        MERGE (c:Coach {id:$id}) SET c.name='Sam'
        MERGE (c)-[:AUTHORED]->(e:CoachExercise {id:$eid})
        SET e.name=$name, e.pattern=$pattern, e.sets=$sets, e.reps=$reps,
            e.notes=$notes, e.created=coalesce(e.created, datetime())
        """,
        id=_COACH_ID, eid=_clib_id(name), name=name, pattern=body.get("pattern", ""),
        sets=body.get("sets"), reps=body.get("reps", ""), notes=body.get("notes", ""),
    )


def _ensure_coach_library_seed() -> None:
    run("MERGE (c:Coach {id:$id}) SET c.name='Sam'", id=_COACH_ID)
    for ex in _COACH_SEED:
        _coach_library_upsert(ex)


@app.get("/coach/library")
def coach_library() -> dict:
    _ensure_coach_library_seed()
    rows = run(
        """
        MATCH (:Coach {id:$id})-[:AUTHORED]->(e:CoachExercise)
        RETURN e.id AS id, e.name AS name, e.pattern AS pattern,
               e.sets AS sets, e.reps AS reps, e.notes AS notes
        ORDER BY e.created
        """,
        id=_COACH_ID,
    )
    for r in rows:
        r["region"] = _region_for(r.get("pattern"), r.get("name"))
    return {"coach": "Sam", "count": len(rows), "exercises": rows}


@app.post("/coach/library")
def coach_library_add(body: dict = Body(default={})) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    _coach_library_upsert(body)
    return {"added": name}
