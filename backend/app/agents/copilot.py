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
    "You are a fitness coach's copilot. The key facts are ALREADY shown to the coach "
    "as labeled stats — do NOT repeat them. Add at most ONE short, plain-text sentence: "
    "an interpretation or a concrete recommendation, grounded only in the provided "
    "member_data. The `conversation` field (if present) is the prior turns, for "
    "resolving follow-ups like 'what about her sleep?' — use it ONLY to understand what "
    "the coach means; every claim must still come from member_data, never from the "
    "conversation. Plain text ONLY — no markdown, asterisks, bold, headings, bullets, or "
    "preamble. Never invent or mention missing data. If there's nothing useful to add, "
    "output nothing."
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
               m.preferred_session_minutes AS preferred_session_minutes,
               m.training_days_per_week AS training_days_per_week,
               collect(DISTINCT g.text) AS goals,
               collect(DISTINCT i.region) AS injuries
        """,
        id=member_id,
    )
    return rows[0] if rows else {}


_ARROW = {"declining": "↓", "improving": "↑", "steady": "→"}


def facts(intent: str, ctx: dict) -> list[dict]:
    """The deterministic diagnostic — the key numbers a coach wants FIRST, straight
    from the graph (the LLM never produces these). Order matters: most important first."""
    p = ctx.get("profile") or {}
    out: list[dict] = []

    def add(label, value):
        if value not in (None, "", [], {}):
            out.append({"label": label, "value": value})

    # session prescription signals — first, for "what length / how often" questions
    if intent in ("general", "brief"):
        if p.get("preferred_session_minutes"):
            add("Preferred session", f"{p['preferred_session_minutes']} min")
        add("Days / week", p.get("training_days_per_week"))

    if intent == "adherence":
        w = ctx.get("weekly_completion_pct") or []
        if w:
            add("Latest adherence", f"{w[-1]['pct']}% {_ARROW.get(ctx.get('trend'), '')}".strip())
            add("Last 4 weeks", " · ".join(f"{x['pct']}%" for x in w[-4:]))
    elif intent in ("general", "churn", "what_changed", "brief"):
        a = ctx.get("adherence") or {}
        if a.get("latest_pct") is not None:
            add("Adherence", f"{a['latest_pct']}% {_ARROW.get(a.get('trend'), '')}".strip())

    if intent == "sleep":
        s = ctx.get("sleep_hours_last_7_days")
        if s:
            add("Avg sleep", f"{s['avg_h']} h")
        add("Goal", ctx.get("sleep_goal"))
        o = ctx.get("oura")
        if o:
            add("Oura sleep score", o.get("avg_sleep_score"))

    if intent == "churn":
        add("Churn risk", (ctx.get("churn") or {}).get("level"))
    if intent == "brief":
        b = ctx.get("brief") or {}
        add("Churn risk", b.get("churn_level"))

    if intent in ("general", "brief", "churn", "what_changed"):
        inj = p.get("injuries") or []
        if inj:
            add("Injuries", ", ".join(inj))

    return out


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
        "MATCH (m:Member {id: $id})-[:HAS_OURA_READING]->(o:OuraReading) "
        "RETURN o.date AS date, o.sleep_score AS sleep_score, "
        "o.readiness_score AS readiness ORDER BY o.date", id=member_id,
    )
    base = run(
        "MATCH (m:Member {id: $id}) RETURN m.sleep_hours_last_7_days AS hours, "
        "m.hrv_ms AS hrv, m.resting_hr_bpm AS resting_hr", id=member_id,
    )
    hours = (base[0]["hours"] if base else None) or []
    sleep_hours = None
    if hours:
        sleep_hours = {"avg_h": round(sum(hours) / len(hours), 1),
                       "min_h": min(hours), "max_h": max(hours), "nights": len(hours)}
    goal = run(
        "MATCH (m:Member {id: $id})-[:HAS_GOAL]->(g) "
        "WHERE toLower(g.text) CONTAINS 'sleep' RETURN g.text AS t LIMIT 1", id=member_id,
    )
    summ = longitudinal.summary(member_id)
    return {
        "profile": _profile(member_id),
        "sleep_hours_last_7_days": sleep_hours,    # avg/min/max or None
        "oura": summ.get("oura"),                  # None when no wearable
        "resting_hr_bpm": base[0]["resting_hr"] if base else None,
        "hrv_ms": base[0]["hrv"] if base else None,
        "sleep_goal": goal[0]["t"] if goal else None,
    }


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


# --- chat history (PRD: the coach can see past chat history + images) ---------

def chat_history(member_id: str) -> list[dict]:
    """The member's real message thread, oldest first — including attachment
    captions (synthetic placeholders; no image bytes in this dataset)."""
    return run(
        """
        MATCH (m:Member {id: $id})-[:SAID]->(c:ChatMessage)
        RETURN c.from AS from, c.ts AS ts, c.text AS text,
               coalesce(c.has_attachment, false) AS has_attachment,
               coalesce(c.attachment_captions, []) AS attachments
        ORDER BY c.ts
        """,
        id=member_id,
    )


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
    ctx = final["context"]
    return ({"intent": final["intent"], "context": ctx,
             "facts": facts(final["intent"], _clean(ctx))}, trace.as_list())


def _clean(obj):
    """Drop null/empty values so the LLM never sees — or narrates — an absent field."""
    if isinstance(obj, dict):
        out = {k: _clean(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {}, "")}
    if isinstance(obj, list):
        return [_clean(v) for v in obj if v not in (None, "")]
    return obj


def _fallback_answer(intent: str, ctx: dict, name: str) -> str:
    """Deterministic grounded answer (no-key path, and the data-thin short-circuit)."""
    if intent == "sleep":
        s = ctx.get("sleep_hours_last_7_days")
        if s:
            goal = f" (goal: {ctx['sleep_goal']})" if ctx.get("sleep_goal") else ""
            return (f"{name} averaged {s['avg_h']} h over {s['nights']} nights "
                    f"(range {s['min_h']}–{s['max_h']} h){goal}.")
        if ctx.get("oura"):
            o = ctx["oura"]
            return (f"{name}'s Oura: avg sleep score {o['avg_sleep_score']}, "
                    f"readiness {o['latest_readiness']} ({o['sleep_score_trend']}).")
    if intent == "adherence":
        a = ctx.get("weekly_completion_pct") or []
        if a:
            return f"{name}'s adherence is {ctx.get('trend', '—')}; latest week {a[-1]['pct']}%."
    if intent == "churn" and ctx.get("churn"):
        ch = ctx["churn"]
        return f"Churn risk is {ch.get('level')}: {'; '.join(ch.get('reasons') or [])}."
    if intent == "brief" and ctx.get("brief"):
        tasks = "; ".join(t["text"] for t in ctx["brief"].get("tasks", []) if t.get("text"))
        return f"Morning brief for {name}: {tasks}"
    return f"Here's what's on file for {name}."


def answer_stream(question: str, result: dict, history: list[dict] | None = None):
    """Stream a grounded answer. Cleans the retrieved slice, returns a coherent
    deterministic line when there is no relevant data (no slow LLM ramble), and
    otherwise lets the LLM phrase ONLY what is present. `history` (recent prior
    turns) gives the model context to resolve follow-ups — never new facts."""
    ctx = _clean(result["context"])
    intent = result["intent"]
    name = (ctx.get("profile") or {}).get("name", "the member")

    if not any(k != "profile" for k in ctx):
        yield f"There's no {intent.replace('_', ' ')} data on file for {name} yet."
        return
    if not llm.is_available():
        yield _fallback_answer(intent, ctx, name)
        return
    payload = {"question": question, "member_data": ctx}
    if history:
        # last few turns only, each trimmed — context for follow-ups, not a token sink
        payload["conversation"] = [{"role": h.get("role"), "text": (h.get("text") or "")[:280]}
                                   for h in history[-6:]]
    yield from llm.stream(COPILOT_SYSTEM, json.dumps(payload, default=str), max_tokens=120)
