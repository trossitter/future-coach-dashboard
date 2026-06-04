"""Surface B — Coach AI Copilot (LangGraph) over KG2.

    route ─▶ retrieve ─▶ END        (answer is streamed separately)

Same discipline as the generator: deterministic intent routing and graph
retrieval pull the *relevant member slice*; the LLM only phrases an answer that
is grounded ONLY in that retrieved data — it never invents. Works without a key
(a deterministic templated answer). Chart intents return a structured series the
frontend renders.
"""
from __future__ import annotations

import json
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .. import llm, longitudinal
from ..db import run
from ..embeddings import embed_query
from ..observability import Trace

COPILOT_SYSTEM = (
    "You are a fitness coach's AI copilot. Answer the coach's question using ONLY "
    "the provided member_data, which was retrieved from the member's knowledge "
    "graph. Be concise and specific — cite the actual numbers. If the data does "
    "not contain the answer, say you don't have that data; never invent. No preamble."
)

# (intent, keywords) — first match wins; order matters.
ROUTES = [
    ("brief", ["brief", "morning", "catch me up", "what should i", "where are we", "today"]),
    ("sleep", ["sleep", "oura", "readiness", "rested", "recovery", "hrv"]),
    ("churn", ["churn", "at risk", "drop off", "drop-off", "cancel", "disengag", "retention", "losing"]),
    ("adherence", ["adherence", "consistency", "completion", "showing up", "missed", "skipping", "compliance"]),
    ("what_changed", ["changed", "change", "since last", "different", "this week vs", "trend"]),
]


class CState(TypedDict, total=False):
    member_id: str
    question: str
    trace: Trace
    intent: str
    context: dict


def _route_intent(q: str) -> str:
    ql = q.lower()
    for intent, kws in ROUTES:
        if any(k in ql for k in kws):
            return intent
    return "general"


def route(state: CState) -> dict:
    with state["trace"].step("agent", "router"):
        intent = _route_intent(state["question"])
        state["trace"].add("decision", "intent", intent=intent)
    return {"intent": intent}


def retrieve(state: CState) -> dict:
    member_id, intent = state["member_id"], state["question"] and state["intent"]
    with state["trace"].step("agent", "retriever", intent=intent):
        ctx = _RETRIEVERS.get(intent, _retrieve_general)(member_id, state["question"])
        state["trace"].add("tool", f"KG2 retrieval: {intent}",
                           keys=list(ctx.keys()))
    return {"context": ctx}


# --- per-intent retrieval (the relevant KG2 slice) ---------------------------

def _profile(member_id: str) -> dict:
    rows = run(
        """
        MATCH (m:Member {id: $id})
        OPTIONAL MATCH (m)-[:HAS_GOAL]->(g:Goal)
        OPTIONAL MATCH (m)-[:HAS_INJURY]->(i:Injury)
        RETURN m.name AS name, m.tier AS tier, m.adherence_trend AS adherence_trend,
               collect(DISTINCT g.text) AS goals,
               collect(DISTINCT i.region) AS injuries
        """,
        id=member_id,
    )
    return rows[0] if rows else {}


def _retrieve_brief(member_id: str, _q: str) -> dict:
    rows = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_BRIEF]->(b:CoachBrief)
        OPTIONAL MATCH (b)-[:HAS_TASK]->(t:MorningTask)
        RETURN b.generated_for AS date, b.churn_level AS churn_level,
               b.churn_reasons AS churn_reasons,
               collect({type: t.type, text: t.text}) AS tasks
        ORDER BY b.generated_for DESC LIMIT 1
        """,
        id=member_id,
    )
    return {"profile": _profile(member_id), "brief": rows[0] if rows else None}


def _retrieve_adherence(member_id: str, _q: str) -> dict:
    weeks = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_ADHERENCE_WEEK]->(w:AdherenceWeek)
        RETURN w.week_of AS week_of, w.pct AS pct ORDER BY w.week_of
        """,
        id=member_id,
    )
    summ = longitudinal.summary(member_id)
    return {"profile": _profile(member_id), "weekly_completion_pct": weeks,
            "trend": summ.get("adherence", {}).get("trend"),
            "journey_stage": summ.get("journey_stage")}


def _retrieve_sleep(member_id: str, _q: str) -> dict:
    oura = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_OURA_READING]->(o:OuraReading)
        RETURN o.date AS date, o.sleep_score AS sleep_score,
               o.readiness_score AS readiness, o.total_sleep_h AS hours,
               o.hrv_ms AS hrv ORDER BY o.date
        """,
        id=member_id,
    )
    base = run(
        "MATCH (m:Member {id: $id}) RETURN m.sleep_hours_last_7_days AS hours, "
        "m.oura_device AS device, m.hrv_ms AS hrv, m.resting_hr_bpm AS resting_hr",
        id=member_id,
    )
    summ = longitudinal.summary(member_id)
    return {"profile": _profile(member_id), "oura_daily": oura,
            "oura_summary": summ.get("oura"),
            "sleep_hours_last_7_days": base[0]["hours"] if base else None,
            "device": base[0]["device"] if base else None,
            "hrv_ms": base[0]["hrv"] if base else None,
            "resting_hr_bpm": base[0]["resting_hr"] if base else None}


def _retrieve_churn(member_id: str, _q: str) -> dict:
    rows = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_BRIEF]->(b:CoachBrief)
        RETURN b.churn_level AS level, b.churn_reasons AS reasons LIMIT 1
        """,
        id=member_id,
    )
    summ = longitudinal.summary(member_id)
    return {"profile": _profile(member_id),
            "churn": rows[0] if rows else None,
            "adherence": summ.get("adherence"), "journey_stage": summ.get("journey_stage")}


def _retrieve_what_changed(member_id: str, _q: str) -> dict:
    summ = longitudinal.summary(member_id)
    weeks = summ.get("adherence", {}).get("weeks", [])
    sessions = run(
        """
        MATCH (m:Member {id: $id})-[:PERFORMED]->(s:Session)
        RETURN s.date AS date, s.title AS title, s.completed AS completed,
               s.rpe AS rpe ORDER BY s.date DESC LIMIT 4
        """,
        id=member_id,
    )
    return {"profile": _profile(member_id),
            "adherence_recent": weeks[-3:], "adherence_trend": summ.get("adherence", {}).get("trend"),
            "recent_sessions": sessions, "oura_summary": summ.get("oura"),
            "journey_stage": summ.get("journey_stage")}


def _retrieve_general(member_id: str, question: str) -> dict:
    # GraphRAG: vector search over the member's chat history + structured context.
    chat = []
    try:
        qv = embed_query(question)
        chat = run(
            """
            CALL db.index.vector.queryNodes('chat_embedding', 10, $vec)
            YIELD node, score
            WHERE node.member_id = $id
            RETURN node.text AS text, node.from AS from, round(score, 3) AS score
            ORDER BY score DESC LIMIT 4
            """,
            vec=qv, id=member_id,
        )
    except Exception:
        pass
    summ = longitudinal.summary(member_id)
    return {"profile": _profile(member_id), "relevant_chat": chat,
            "journey_stage": summ.get("journey_stage"),
            "adherence": summ.get("adherence"), "oura_summary": summ.get("oura")}


_RETRIEVERS = {
    "brief": _retrieve_brief,
    "adherence": _retrieve_adherence,
    "sleep": _retrieve_sleep,
    "churn": _retrieve_churn,
    "what_changed": _retrieve_what_changed,
    "general": _retrieve_general,
}


# --- charts (structured series for the frontend) -----------------------------

def chart(member_id: str, kind: str) -> dict:
    if kind == "adherence":
        rows = run(
            "MATCH (m:Member {id:$id})-[:HAS_ADHERENCE_WEEK]->(w) "
            "RETURN w.week_of AS week_of, w.pct AS pct ORDER BY w.week_of", id=member_id)
        return {"type": "line", "title": "Weekly adherence (%)", "x": "week_of",
                "y": ["pct"], "series": rows}
    if kind == "sleep":
        rows = run(
            "MATCH (m:Member {id:$id})-[:HAS_OURA_READING]->(o) "
            "RETURN o.date AS date, o.sleep_score AS sleep_score, "
            "o.readiness_score AS readiness ORDER BY o.date", id=member_id)
        return {"type": "line", "title": "Oura sleep & readiness", "x": "date",
                "y": ["sleep_score", "readiness"], "series": rows}
    if kind == "weight":
        rows = run(
            "MATCH (m:Member {id:$id})-[:HAS_WEIGHT_SAMPLE]->(w) "
            "RETURN w.date AS date, w.kg AS kg ORDER BY w.date", id=member_id)
        return {"type": "line", "title": "Weight (kg)", "x": "date",
                "y": ["kg"], "series": rows}
    if kind == "messages":
        rows = run(
            "MATCH (m:Member {id:$id})-[:SAID]->(c:ChatMessage) "
            "WITH substring(c.ts,0,10) AS day, count(*) AS n "
            "RETURN day, n ORDER BY day", id=member_id)
        return {"type": "bar", "title": "Messages per day", "x": "day",
                "y": ["n"], "series": rows}
    return {"type": "none", "title": f"unknown chart: {kind}", "series": []}


# --- graph + run -------------------------------------------------------------

def _build():
    g = StateGraph(CState)
    g.add_node("route", route)
    g.add_node("retrieve", retrieve)
    g.set_entry_point("route")
    g.add_edge("route", "retrieve")
    g.add_edge("retrieve", END)
    return g.compile()


GRAPH = _build()


def run_copilot(member_id: str, question: str) -> tuple[dict, list[dict]]:
    trace = Trace()
    final = GRAPH.invoke({"member_id": member_id, "question": question, "trace": trace})
    return {"intent": final["intent"], "context": final["context"]}, trace.as_list()


def _fallback_answer(question: str, result: dict) -> str:
    """Deterministic grounded answer when no LLM key is present."""
    c, intent = result["context"], result["intent"]
    name = (c.get("profile") or {}).get("name", "the member")
    if intent == "adherence":
        w = c.get("weekly_completion_pct") or []
        latest = w[-1]["pct"] if w else "n/a"
        return f"{name}'s adherence is trending {c.get('trend')}; latest week {latest}%."
    if intent == "sleep" and c.get("oura_summary"):
        o = c["oura_summary"]
        return (f"{name}'s Oura: avg sleep score {o['avg_sleep_score']}, latest "
                f"{o['latest_sleep_score']}, readiness {o['latest_readiness']} ({o['sleep_score_trend']}).")
    if intent == "churn" and c.get("churn"):
        ch = c["churn"]
        return f"Churn risk is {ch.get('level')}: {'; '.join(ch.get('reasons') or [])}."
    if intent == "brief" and c.get("brief"):
        tasks = "; ".join(t["text"] for t in c["brief"].get("tasks", []) if t.get("text"))
        return f"Morning brief for {name}: {tasks}"
    return f"Retrieved {intent} data for {name}. (LLM key not set — showing raw grounding.)"


def answer_stream(question: str, result: dict):
    """Stream the grounded answer; deterministic single chunk if no key."""
    if not llm.is_available():
        yield _fallback_answer(question, result)
        return
    user = json.dumps({"question": question, "intent": result["intent"],
                       "member_data": result["context"]}, default=str)
    yield from llm.stream(COPILOT_SYSTEM, user, max_tokens=500)
