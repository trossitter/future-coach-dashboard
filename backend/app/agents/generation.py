"""Surface A — multi-agent workout-generation crew (LangGraph).

A StateGraph composes distinct agent roles:

    plan ─▶ retrieve ─▶ assemble ─▶ safety_review ─▶ narrate ─▶ END
                            ▲              │ (critic loop)
                            └──────────────┘  ids ⊄ safe → reassemble

Safety is NEVER the LLM's call: candidate selection comes from the graph
(`safety.eligible`), and the safety-reviewer validates that every prescribed
exercise id is in the graph-derived safe set, looping back to a deterministic
rebuild if the LLM drifted. The LLM only resolves intent, shapes sets/reps, and
phrases the result — and the whole crew degrades gracefully without an API key.
"""
from __future__ import annotations

import json
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .. import llm, longitudinal, resolver, safety
from ..db import run
from ..observability import Trace
from ..schemas import WorkoutPlan

ASSEMBLER_SYSTEM = (
    "You assemble a safe, structured workout from a PRE-VETTED candidate list. "
    "Use ONLY exercise ids from the provided candidates — never invent ids. "
    "Fill warmup, main, and cooldown to roughly the requested counts, set "
    "sensible sets/reps/rest, and respect the journey-stage guidance (e.g. "
    "lower volume for onboarding/at-risk members)."
)


class GenState(TypedDict, total=False):
    member_id: str
    prompt: str
    time_minutes: int
    exclude_terms: list[str]
    trace: Trace
    journey: dict
    intent: dict
    candidates: list[dict]
    safe_ids: list[str]
    plan: dict
    provenance: list[dict]
    filtered: list[dict]
    narration: str
    degraded: bool
    revisions: int
    needs_revision: bool
    force_deterministic: bool


# --- helpers -----------------------------------------------------------------

def _exercise_meta(ids: list[str]) -> dict[str, dict]:
    rows = run(
        """
        MATCH (e:Exercise) WHERE e.id IN $ids
        OPTIONAL MATCH (e)-[:TARGETS]->(mu:Muscle)
        OPTIONAL MATCH (e)-[:HAS_PATTERN]->(p:MovementPattern)
        RETURN e.id AS id, collect(DISTINCT mu.name) AS muscles,
               collect(DISTINCT p.name) AS patterns
        """,
        ids=ids,
    )
    return {r["id"]: {"muscles": r["muscles"], "patterns": r["patterns"]} for r in rows}


def _counts(time_minutes: int) -> dict:
    main = max(3, min(7, (time_minutes - 12) // 7))
    return {"warmup": 2, "main": main, "cooldown": 2}


def _bucket(patterns: list[str]) -> str:
    ps = " ".join(patterns).lower()
    if any(k in ps for k in ("mobility - dynamic", "activation", "balance")):
        return "warmup"
    if any(k in ps for k in ("mobility - static", "regen", "yoga", "massage", "static")):
        return "cooldown"
    return "main"


def _rx(section: str, stage: str) -> tuple[int, str, int]:
    if section == "warmup":
        return 1, "8-10 reps", 20
    if section == "cooldown":
        return 1, "30-45s hold", 15
    sets = 2 if stage in ("onboarding", "at_risk") else 3
    rest = 60 if stage in ("onboarding", "at_risk") else 90
    return sets, "8-12 reps", rest


def _prescribe(c: dict, section: str, stage: str) -> dict:
    sets, reps, rest = _rx(section, stage)
    return {"id": c["id"], "name": c["name"], "section": section,
            "sets": sets, "reps": reps, "rest_seconds": rest}


def _deterministic_plan(cands: list[dict], counts: dict, stage: str) -> dict:
    by_bucket = {"warmup": [], "main": [], "cooldown": []}
    for c in cands:
        by_bucket[_bucket(c.get("patterns", []))].append(c)
    plan = {"warmup": [], "main": [], "cooldown": []}
    used: set[str] = set()
    for section in ("main", "warmup", "cooldown"):  # main first, it's the priority
        pool = [c for c in by_bucket[section] if c["id"] not in used] or \
               [c for c in cands if c["id"] not in used]
        for c in pool[: counts[section]]:
            plan[section].append(_prescribe(c, section, stage))
            used.add(c["id"])
    return plan


def _llm_plan(prompt: str, intent: dict, journey: dict, cands: list[dict], counts: dict):
    payload = {
        "request": prompt,
        "intent": intent,
        "journey_stage": journey.get("journey_stage"),
        "guidance": journey.get("generation_bias"),
        "counts": counts,
        "candidates": [
            {"id": c["id"], "name": c["name"],
             "muscles": c.get("muscles", []), "patterns": c.get("patterns", [])}
            for c in cands[:25]
        ],
    }
    plan = llm.parse(ASSEMBLER_SYSTEM, json.dumps(payload), WorkoutPlan, max_tokens=2500)
    return plan.model_dump() if plan else None


# --- agents (graph nodes) ----------------------------------------------------

def plan(state: GenState) -> dict:
    trace, member_id = state["trace"], state["member_id"]
    with trace.step("agent", "planner"):
        journey = longitudinal.summary(member_id)
        # Concept resolution is the graph's job, not the LLM's — alias-aware and
        # deterministic (no API call), which is also a big latency win.
        mrows = run("MATCH (m:Muscle) RETURN m.name AS name, coalesce(m.alt_labels,[]) AS alts")
        surface: dict[str, str] = {}
        for r in mrows:
            surface[r["name"].lower()] = r["name"]
            for a in r["alts"]:
                surface[a.lower()] = r["name"]
        patterns = [r["name"] for r in run("MATCH (p:MovementPattern) RETURN p.name AS name")]
        intent = _resolve_intent(state["prompt"], surface, patterns)
        dis = run("MATCH (m:Member {id:$id}) RETURN coalesce(m.dislikes,[]) AS d",
                  id=member_id)
        dislikes = dis[0]["d"] if dis else []
        intent["exclude_terms"] = sorted({*intent.get("exclude_terms", []),
                                          *state.get("exclude_terms", []), *dislikes})
        trace.add("tool", "resolver + longitudinal (deterministic planner)",
                  stage=journey.get("journey_stage"))
    return {"journey": journey, "intent": intent}


def _resolve_intent(prompt: str, muscle_surface: dict[str, str], patterns: list[str]) -> dict:
    pl = f" {prompt.lower()} "
    tm = sorted({canon for surf, canon in muscle_surface.items()
                 if surf in pl or f" {surf} " in pl})
    tp = sorted({p for p in patterns if p in pl or p.split(" - ")[0] in pl})
    return {"target_muscles": tm, "target_patterns": tp,
            "exclude_terms": [], "emphasis": "", "summary": prompt[:140]}


def retrieve(state: GenState) -> dict:
    trace, member_id, intent = state["trace"], state["member_id"], state["intent"]
    with trace.step("agent", "retriever"):
        eligible = safety.eligible(member_id, exclude_terms=intent.get("exclude_terms") or None)
        safe_ids = [e["id"] for e in eligible]
        meta = _exercise_meta(safe_ids)
        sem = {r["id"]: r["score"] for r in resolver.semantic_exercise_search(state["prompt"], k=50)}
        tm, tp = set(intent.get("target_muscles", [])), set(intent.get("target_patterns", []))
        cands = []
        for e in eligible:
            m = meta.get(e["id"], {})
            boost = 0.1 * len(tm & set(m.get("muscles", []))) + 0.1 * len(tp & set(m.get("patterns", [])))
            cands.append({**e, **m, "score": round(sem.get(e["id"], 0.0) + boost, 3)})
        cands.sort(key=lambda c: c["score"], reverse=True)
        trace.add("tool", "safety.eligible (graph)", eligible=len(safe_ids))
        trace.add("tool", "vector.search", ranked=len(sem))
    return {"candidates": cands, "safe_ids": safe_ids}


def assemble(state: GenState) -> dict:
    trace = state["trace"]
    counts = _counts(state["time_minutes"])
    with trace.step("agent", "assembler"):
        plan_dict, degraded = None, False
        if state.get("force_deterministic"):
            plan_dict = _deterministic_plan(state["candidates"], counts, state["journey"].get("journey_stage", ""))
            trace.add("critic", "rebuilt deterministically")
        elif llm.is_available():
            plan_dict = _llm_plan(state["prompt"], state["intent"], state["journey"],
                                  state["candidates"], counts)
        if plan_dict is None:
            plan_dict = _deterministic_plan(state["candidates"], counts, state["journey"].get("journey_stage", ""))
            degraded = not llm.is_available()
    return {"plan": plan_dict, "degraded": degraded}


def safety_review(state: GenState) -> dict:
    trace, member_id = state["trace"], state["member_id"]
    plan_dict, safe = state["plan"], set(state["safe_ids"])
    revisions = state.get("revisions", 0)
    with trace.step("agent", "safety_reviewer"):
        invalid = []
        for section in ("warmup", "main", "cooldown"):
            kept = []
            for p in plan_dict.get(section, []):
                (kept if p["id"] in safe else invalid).append(p if p["id"] in safe else p["id"])
            plan_dict[section] = [p for p in plan_dict.get(section, []) if p["id"] in safe]
        trace.add("check", "ids ⊆ graph-safe set", invalid=len(invalid))
        # de-dupe across sections — no exercise should appear twice
        seen: set[str] = set()
        for section in ("warmup", "main", "cooldown"):
            deduped = []
            for p in plan_dict.get(section, []):
                if p["id"] not in seen:
                    seen.add(p["id"])
                    deduped.append(p)
            plan_dict[section] = deduped
        if (invalid or not plan_dict.get("main")) and revisions == 0:
            trace.add("critic", "drift detected → revise")
            return {"plan": plan_dict, "revisions": revisions + 1,
                    "needs_revision": True, "force_deterministic": True}
        provenance = _provenance(state, plan_dict)
        filtered = _filtered_out(member_id, state["intent"])
    return {"plan": plan_dict, "provenance": provenance, "filtered": filtered,
            "needs_revision": False}


def _provenance(state: GenState, plan_dict: dict) -> list[dict]:
    intent = state["intent"]
    tm, tp = set(intent.get("target_muscles", [])), set(intent.get("target_patterns", []))
    meta = {c["id"]: c for c in state["candidates"]}
    out = []
    for section in ("warmup", "main", "cooldown"):
        for p in plan_dict.get(section, []):
            c = meta.get(p["id"], {})
            because = []
            hit_m = tm & set(c.get("muscles", []))
            hit_p = tp & set(c.get("patterns", []))
            if hit_m:
                because.append(f"targets requested muscle(s): {', '.join(sorted(hit_m))}")
            if hit_p:
                because.append(f"matches movement pattern(s): {', '.join(sorted(hit_p))}")
            if c.get("score"):
                because.append(f"semantic match to the prompt (score {c['score']})")
            out.append({
                "exercise_id": p["id"], "name": p["name"],
                "chosen_because": because or ["fits the available safe pool"],
                "safe_because": ["does not load an injured joint (part-of checked)",
                                 "movement pattern not contraindicated",
                                 "all required equipment available"],
            })
    return out


def _filtered_out(member_id: str, intent: dict, limit: int = 5) -> list[dict]:
    """Show what the safety filter removed that the coach might have expected —
    contraindicated exercises matching the intent, with reasons + alternatives."""
    contra = safety.contraindicated(member_id)
    tm = set(intent.get("target_muscles", []))
    relevant = contra
    if tm:
        ids = [c["id"] for c in contra]
        meta = _exercise_meta(ids)
        relevant = [c for c in contra if tm & set(meta.get(c["id"], {}).get("muscles", []))] or contra
    out = []
    for c in relevant[:limit]:
        alts = safety.alternatives(member_id, c["id"], limit=2)
        out.append({"id": c["id"], "name": c["name"], "reasons": c["reasons"],
                    "alternatives": [a["name"] for a in alts]})
    return out


NARRATOR_SYSTEM = (
    "You are a fitness coach. In 2-3 sentences, explain this workout to the coach: "
    "what it targets, how it respects the member's journey stage, and that unsafe "
    "options were filtered out by the graph. Be concrete and warm; no preamble."
)


def narration_stream(prompt: str, result: dict):
    """Stream the coach-facing narration, decoupled from the blocking plan so the
    structured plan returns fast and prose streams after. Empty if no key."""
    if not llm.is_available():
        return
    plan_dict = result["plan"]
    names = {s: [p["name"] for p in plan_dict.get(s, [])]
             for s in ("warmup", "main", "cooldown")}
    user = json.dumps({"request": prompt, "journey_stage": result["journey_stage"],
                       "plan": names,
                       "filtered_for_safety": [f["name"] for f in result["filtered_out"]]})
    yield from llm.stream(NARRATOR_SYSTEM, user, max_tokens=400)


def _route(state: GenState) -> str:
    return "revise" if state.get("needs_revision") else "done"


def _build():
    g = StateGraph(GenState)
    g.add_node("plan", plan)
    g.add_node("retrieve", retrieve)
    g.add_node("assemble", assemble)
    g.add_node("safety_review", safety_review)
    g.set_entry_point("plan")
    g.add_edge("plan", "retrieve")
    g.add_edge("retrieve", "assemble")
    g.add_edge("assemble", "safety_review")
    g.add_conditional_edges("safety_review", _route, {"revise": "assemble", "done": END})
    return g.compile()


GRAPH = _build()


def run_generation(member_id: str, prompt: str, time_minutes: int = 45,
                   exclude_terms: list[str] | None = None) -> tuple[dict, list[dict]]:
    trace = Trace()
    init: GenState = {
        "member_id": member_id, "prompt": prompt, "time_minutes": time_minutes,
        "exclude_terms": exclude_terms or [], "trace": trace, "revisions": 0,
    }
    final = GRAPH.invoke(init)
    result = {
        "member_id": member_id,
        "intent": final["intent"],
        "plan": final["plan"],
        "provenance": final.get("provenance", []),
        "filtered_out": final.get("filtered", []),
        "journey_stage": final["journey"].get("journey_stage", "unknown"),
        "narration": final.get("narration", ""),
        "degraded": final.get("degraded", False),
    }
    return result, trace.as_list()
