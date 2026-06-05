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
from ..schemas import RouteDecision

COPILOT_SYSTEM = (
    "You are a fitness coach's copilot. The key facts are ALREADY shown to the coach as "
    "numbered, labeled stats — the `sources` list, [1], [2], … — so do NOT repeat them. "
    "Add at most ONE short, plain-text sentence: an interpretation or a concrete "
    "recommendation. EVERY claim must be grounded in the numbered sources, and you MUST "
    "cite the source number(s) you rely on inline, e.g. 'her vitamin D is the one to move "
    "on first [3].' Cite only those numbers; never assert anything you cannot cite. The "
    "`conversation` field (if present) is the prior turns, for resolving follow-ups like "
    "'what about her sleep?' — use it ONLY to understand what the coach means; every claim "
    "still comes from the sources. Plain text ONLY — no markdown, asterisks, bold, "
    "headings, bullets, or preamble. Never invent or mention missing data. If no source "
    "supports a useful point, output nothing."
)

# (intent, keywords) — first match wins; order matters. The deterministic
# fallback router, used when there's no API key (or the LLM router errors).
ROUTES = [
    ("brief", ["brief", "morning", "catch me up", "what should i", "where are we", "today"]),
    ("sleep", ["sleep", "oura", "readiness", "rested", "recovery", "hrv"]),
    ("labs", ["lab", "blood", "dexa", "cholesterol", "ldl", "hdl", "triglyceride",
              "a1c", "hba1c", "glucose", "vitamin d", "ferritin", "crp", "bone density",
              "body fat", "lean mass", "visceral", "bloodwork", "biomarker panel",
              "test result", "test came back", "diagnos"]),
    ("churn", ["churn", "at risk", "drop off", "drop-off", "cancel", "disengag", "retention", "losing"]),
    ("adherence", ["adherence", "consistency", "completion", "showing up", "missed", "skipping", "compliance"]),
    ("what_changed", ["changed", "change", "since last", "different", "this week vs", "trend"]),
]

VALID_INTENTS = {"brief", "sleep", "labs", "adherence", "churn", "what_changed", "general"}
ROUTE_CONFIDENCE = 0.70   # below this, ask rather than guess

ROUTER_SYSTEM = (
    "Classify a fitness coach's question about ONE of their members into exactly one "
    "intent, and rate your confidence 0.0–1.0.\n"
    "Intents:\n"
    "- brief: a morning catch-up / 'what should I know today'\n"
    "- sleep: sleep, recovery, readiness, HRV\n"
    "- labs: blood panel / DEXA / lab results — cholesterol, HbA1c, vitamin D, bone "
    "density, body composition, 'my test came back', navigating a diagnosis\n"
    "- adherence: consistency, showing up, completion, missed sessions\n"
    "- churn: retention / at-risk / likely to cancel\n"
    "- what_changed: what's different since last week / recent trend\n"
    "- general: anything else about the member (preferences, injuries, a past "
    "exercise, why something was programmed, etc.)\n"
    "If the question is genuinely ambiguous between intents, set a LOW confidence and "
    "fill clarify_question with a short either/or question for the coach. Be decisive "
    "when the intent is clear (high confidence)."
)


class CState(TypedDict, total=False):
    member_id: str
    question: str
    trace: Trace
    intent: str
    confidence: float
    clarify_question: str
    context: dict


def _route_intent(q: str) -> str:
    """Deterministic keyword router (no-key fallback)."""
    ql = q.lower()
    for intent, kws in ROUTES:
        if any(k in ql for k in kws):
            return intent
    return "general"


def route(state: CState) -> dict:
    """Deterministic-first routing with an LLM tiebreak. A coach with a full
    roster wants a fast reminder, so the keyword router answers the common case
    in ~0ms with no LLM call. Only when keywords can't tell (the `general`
    fallthrough) do we escalate to the structured RouteDecision — and if even
    that is low-confidence, we route to `clarify` and ask instead of guessing."""
    q = state["question"]
    with state["trace"].step("agent", "router"):
        kw = _route_intent(q)
        if kw != "general":
            state["trace"].add("decision", "intent (keyword, fast path)", intent=kw)
            return {"intent": kw, "confidence": None}

        # keywords missed → escalate to the LLM only here
        if llm.is_available():
            rd = llm.parse(ROUTER_SYSTEM, q, RouteDecision, max_tokens=200)
            decision = rd.model_dump() if rd else None
            if decision:
                intent, conf = decision.get("intent"), decision.get("confidence") or 0.0
                if intent not in VALID_INTENTS or conf < ROUTE_CONFIDENCE:
                    cq = (decision.get("clarify_question") or "").strip()
                    state["trace"].add("decision", "low confidence → clarify",
                                       intent=intent, confidence=round(conf, 2))
                    return {"intent": "clarify", "confidence": conf, "clarify_question": cq}
                state["trace"].add("decision", "intent (llm tiebreak)",
                                   intent=intent, confidence=round(conf, 2))
                return {"intent": intent, "confidence": conf}

        # no key / parse failure → safe default
        state["trace"].add("decision", "intent (keyword: general)", intent="general")
        return {"intent": "general", "confidence": None}


def retrieve(state: CState) -> dict:
    member_id, intent = state["member_id"], state["intent"]
    # A clarify routing has no member slice to fetch — carry the question through.
    if intent == "clarify":
        return {"context": {"clarify": state.get("clarify_question", "")}}
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
    from the graph (the LLM never produces these). Each fact carries its KG `source`
    so the LLM's interpretation can cite it [n] and the coach can trace it back to
    the data. Order matters: most important first."""
    p = ctx.get("profile") or {}
    out: list[dict] = []

    def add(label, value, source):
        if value not in (None, "", [], {}):
            out.append({"label": label, "value": value, "source": source})

    # session prescription signals — first, for "what length / how often" questions
    if intent in ("general", "brief"):
        if p.get("preferred_session_minutes"):
            add("Preferred session", f"{p['preferred_session_minutes']} min", "Profile · preferences")
        add("Days / week", p.get("training_days_per_week"), "Profile · preferences")

    if intent == "adherence":
        w = ctx.get("weekly_completion_pct") or []
        if w:
            add("Latest adherence", f"{w[-1]['pct']}% {_ARROW.get(ctx.get('trend'), '')}".strip(),
                "Adherence · weekly series")
            add("Last 4 weeks", " · ".join(f"{x['pct']}%" for x in w[-4:]),
                "Adherence · weekly series")
    elif intent in ("general", "churn", "what_changed", "brief"):
        a = ctx.get("adherence") or {}
        if a.get("latest_pct") is not None:
            add("Adherence", f"{a['latest_pct']}% {_ARROW.get(a.get('trend'), '')}".strip(),
                "Adherence · weekly series")

    if intent == "sleep":
        s = ctx.get("sleep_hours_last_7_days")
        if s:
            add("Avg sleep", f"{s['avg_h']} h", "Oura · sleep, last 7 nights")
        add("Goal", ctx.get("sleep_goal"), "Goal")
        o = ctx.get("oura")
        if o:
            add("Oura sleep score", o.get("avg_sleep_score"), "Oura readings")

    if intent == "labs":
        flags = ctx.get("flags") or []
        # notable (out-of-range) findings lead; then a couple of normal anchors
        ordered = [f for f in flags if f.get("notable")] + [f for f in flags if not f.get("notable")]
        for f in ordered[:5]:
            src = f.get("source") or "Lab panel · clinical reference bands"
            add(f["marker"], f"{f['value']} {f['unit']} · {f['status']}", src)

    if intent == "churn":
        add("Churn risk", (ctx.get("churn") or {}).get("level"), "Coach brief")
    if intent == "brief":
        b = ctx.get("brief") or {}
        add("Churn risk", b.get("churn_level"), "Coach brief")

    if intent in ("general", "brief", "churn", "what_changed"):
        inj = p.get("injuries") or []
        if inj:
            add("Injuries", ", ".join(inj), "Injuries · KG2→KG1")

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


# Standard clinical reference bands — the interpretation is DETERMINISTIC (code,
# not the LLM). Each entry: (label, status). First band whose upper bound the value
# is under wins. Sources: ACC/AHA lipids, ADA HbA1c, Endocrine Society vitamin D.
def _band(value, bands, unit):
    if value is None:
        return None
    for upper, status in bands:
        if upper is None or value < upper:
            return {"value": value, "unit": unit, "status": status,
                    "notable": status not in ("optimal", "normal", "good", "within range")}
    return None


def _lab_flags(bp: dict, dx: dict) -> list[dict]:
    """Deterministic, clinically-banded interpretation of the raw markers."""
    out = []

    def add(marker, reading):
        if reading:
            out.append({"marker": marker, **reading})

    if bp:
        add("LDL", _band(bp.get("ldl"), [(100, "optimal"), (130, "near-optimal"),
                                          (160, "borderline-high"), (190, "high"), (None, "very high")], "mg/dL"))
        add("HDL", _band(bp.get("hdl"), [(40, "low"), (60, "normal"), (None, "good")], "mg/dL"))
        add("Triglycerides", _band(bp.get("trig"), [(150, "normal"), (200, "borderline-high"),
                                                     (500, "high"), (None, "very high")], "mg/dL"))
        add("HbA1c", _band(bp.get("hba1c"), [(5.7, "normal"), (6.5, "prediabetes"), (None, "diabetes range")], "%"))
        add("Vitamin D", _band(bp.get("vit_d"), [(20, "deficient"), (30, "insufficient"), (None, "sufficient")], "ng/mL"))
        add("CRP", _band(bp.get("crp"), [(1, "low CV risk"), (3, "average CV risk"), (None, "high CV risk")], "mg/L"))
    if dx:
        add("Bone density (Z)", _band(dx.get("bone_z"), [(-2.0, "below expected range"), (None, "within range")], "Z"))
        # body composition is shown as-is (no universal cut-point), lean mass leads
        if dx.get("lean_mass") is not None:
            out.append({"marker": "Lean mass", "value": dx["lean_mass"], "unit": "kg",
                        "status": "tracked", "notable": False})
        add("Visceral fat", _band(dx.get("visceral_fat"), [(100, "within range"), (None, "elevated")], "cm²"))
    return out


def _retrieve_labs(member_id: str, _q: str) -> dict:
    bp = run(
        "MATCH (m:Member {id:$id})-[:HAS_LAB]->(l:BloodPanel) "
        "RETURN l.date AS date, l.ldl_mg_dl AS ldl, l.hdl_mg_dl AS hdl, "
        "l.triglycerides_mg_dl AS trig, l.hba1c_pct AS hba1c, l.crp_mg_l AS crp, "
        "l.vitamin_d_ng_ml AS vit_d, l.ferritin_ng_ml AS ferritin "
        "ORDER BY l.date DESC LIMIT 1", id=member_id)
    dx = run(
        "MATCH (m:Member {id:$id})-[:HAS_LAB]->(l:DexaScan) "
        "RETURN l.date AS date, l.lean_mass_kg AS lean_mass, l.fat_mass_kg AS fat_mass, "
        "l.body_fat_pct AS body_fat, l.visceral_fat_cm2 AS visceral_fat, "
        "l.bone_density_z_score AS bone_z ORDER BY l.date DESC LIMIT 1", id=member_id)
    blood, dexa = (bp[0] if bp else None), (dx[0] if dx else None)
    return {"profile": _profile(member_id), "blood_panel": blood, "dexa": dexa,
            "flags": _lab_flags(blood or {}, dexa or {})}


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
    "labs": _retrieve_labs,
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
    if intent == "labs":
        flags = ctx.get("flags") or []
        notable = [f for f in flags if f.get("notable")]
        if notable:
            bits = ", ".join(f"{f['marker']} {f['value']}{f['unit']} ({f['status']})" for f in notable[:3])
            return f"{name}'s flagged markers: {bits}. Everything else is in range."
        if flags:
            return f"{name}'s latest labs are all within standard ranges."
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

    # Low-confidence routing → ask the coach (the router already drafted the
    # question); no LLM call, no retrieval, no guessing.
    if intent == "clarify":
        yield ctx.get("clarify") or (
            f"I want to pull the right data for {name} — are you asking about their "
            "training, recovery, or retention?")
        return

    if not any(k != "profile" for k in ctx):
        yield f"There's no {intent.replace('_', ' ')} data on file for {name} yet."
        return
    if not llm.is_available():
        # Out of tokens vs. no key are both "no LLM" — but say so differently. The
        # facts above are straight from the graph either way; only commentary stops.
        if llm.budget_exhausted():
            yield ("Cooldown time — we've hit the session's token cap, so I'm "
                   "racking the weights on live commentary for now. The stats above "
                   "came straight from the graph (no AI needed); catch your breath "
                   "and come back in a bit.")
        else:
            yield _fallback_answer(intent, ctx, name)
        return
    payload = {"question": question, "member_data": ctx}
    # The numbered, source-tagged facts the LLM must cite. They ARE the citations:
    # if a claim isn't backed by one of these graph-derived facts, it can't be made.
    src = result.get("facts") or []
    if src:
        payload["sources"] = [
            {"n": i + 1, "fact": f"{s['label']}: {s['value']}", "from": s.get("source", "")}
            for i, s in enumerate(src)
        ]
    if history:
        # last few turns only, each trimmed — context for follow-ups, not a token sink
        payload["conversation"] = [{"role": h.get("role"), "text": (h.get("text") or "")[:280]}
                                   for h in history[-6:]]
    yield from llm.stream(COPILOT_SYSTEM, json.dumps(payload, default=str), max_tokens=140)
