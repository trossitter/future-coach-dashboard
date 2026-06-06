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
    avoid_joints: list[str]
    exclude_equipment: list[str]
    extra_equipment: list[str]
    trace: Trace
    journey: dict
    intent: dict
    candidates: list[dict]
    safe_ids: list[str]
    plan: dict
    provenance: list[dict]
    filtered: list[dict]
    filtered_summary: dict
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


# Equipment polarity cues — a kit mention is only actionable WITH a cue telling us
# whether it's gone ("no bands", "left at home") or available ("he has a bench").
# Equipment is first-class intent here: parsed deterministically like muscles and
# patterns, never inferred by the LLM.
_EQUIP_REMOVAL_CUES = (
    "no ", "without", "doesn't have", "does not have", "don't have", "can't use",
    "cannot use", "lacks", "left at home", "broken",
)
_EQUIP_ADDITION_CUES = (
    "has ", "have ", "got ", "with ", "access to", "available", "owns", "brought",
)
# "only X" / "just X" name the kit that IS on hand — availability, not exclusion.
# Used by the polarity classifier only (NOT the open-vocab clarify gate), so
# "only dumbbells" reads as available without changing what counts as "unknown
# kit worth asking about".
_EQUIP_AVAILABILITY_CUES = ("only ", "just ")
# Clause boundaries. A cue on the far side of one of these does NOT govern an
# equipment mention: in "no barbell, only dumbbells" the comma ends the "no"
# clause, so the removal can't bleed onto the dumbbells named after it.
_CLAUSE_SEPS = (",", ";", " but ", " however ", " though ")


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
        # Name-level exclusions parsed from the prompt ("exclude deadlifts"). The
        # skip-set keeps joint / equipment / muscle / pattern mentions OUT of the
        # name filter, so each stays on its own resolver (joint→clarify gate,
        # equipment→polarity, muscle/pattern→intent) and only true exercise-name
        # terms reach the CONTAINS filter.
        eq_surface = _equipment_surface()
        skip = set(surface) | {v.lower() for v in surface.values()} \
            | set(eq_surface) | {v.lower() for v in eq_surface.values()} \
            | {p.lower() for p in patterns} \
            | {w for p in patterns for w in p.lower().replace(" - ", " ").split()}
        for r in run("MATCH (j:Joint) RETURN j.name AS name, coalesce(j.alt_labels,[]) AS alts"):
            skip.add(r["name"].lower())
            skip.update(a.lower() for a in r["alts"])
        parsed_excl = _resolve_exclude_terms(state["prompt"], skip)
        # This-session exclusions the coach asked for (prompt-parsed + any passed
        # via the request) — kept separate from standing dislikes so the UI can
        # show only what the coach did THIS session, while dislikes stay silently
        # applied (as they always were). All lowercase: `safety.eligible` already
        # lowercases for its CONTAINS match, so this changes no filtering.
        session_excl = sorted({t.lower() for t in (*parsed_excl,
                                                   *state.get("exclude_terms", []))})
        intent["session_exclude_terms"] = session_excl
        intent["exclude_terms"] = sorted({
            t.lower() for t in (*intent.get("exclude_terms", []),
                                *session_excl, *dislikes)})
        # Parse equipment polarity from the prompt and fold it into the session
        # overrides (resolved removals/additions), merged with any passed in via
        # the clarify loop.
        exclude_eq, extra_eq = _resolve_equipment(state["prompt"])
        intent["exclude_equipment"] = sorted({*exclude_eq, *state.get("exclude_equipment", [])})
        intent["extra_equipment"] = sorted({*extra_eq, *state.get("extra_equipment", [])})
        trace.add("tool", "resolver + longitudinal (deterministic planner)",
                  stage=journey.get("journey_stage"))
        trace.add("decision", "equipment overrides",
                  exclude=intent["exclude_equipment"], extra=intent["extra_equipment"])
    return {"journey": journey, "intent": intent}


def _equipment_surface() -> dict[str, str]:
    """Surface form (name + alt_labels, lowercased) -> canonical Equipment name."""
    surface: dict[str, str] = {}
    for r in run("MATCH (e:Equipment) RETURN e.name AS name, coalesce(e.alt_labels,[]) AS alts"):
        surface[r["name"].lower()] = r["name"]
        for a in r["alts"]:
            surface[a.lower()] = r["name"]
    return surface


def _classify_equipment(prompt: str, surface: dict[str, str]) -> tuple[list[str], list[str]]:
    """Pure polarity scan: for each known Equipment surface form in the prompt,
    classify it as excluded or available by the nearest cue to its LEFT *within
    its own clause*. Clause-scoping stops a removal in one clause ("no barbell")
    from bleeding onto kit named in the next ("only dumbbells, a kettlebell").
    Mirrors how `detect_clarifications` scans surface forms — deterministic, no
    LLM, no DB (surface is passed in). Returns (exclude, extra) canonical names."""
    pl = f" {prompt.lower()} "
    exclude: set[str] = set()
    extra: set[str] = set()
    add_cues = _EQUIP_ADDITION_CUES + _EQUIP_AVAILABILITY_CUES
    for surf, canon in surface.items():
        idx = pl.find(f" {surf} ")
        if idx < 0:
            idx = pl.find(f" {surf}")
        if idx < 0:
            continue
        prefix = pl[:idx + 1]
        # restrict the cue search to the mention's own clause: drop everything up
        # to (and including) the last clause separator before it.
        cut = max((prefix.rfind(sep) + len(sep) for sep in _CLAUSE_SEPS
                   if sep in prefix), default=0)
        clause = prefix[cut:]
        rem = max((clause.rfind(c) for c in _EQUIP_REMOVAL_CUES), default=-1)
        add = max((clause.rfind(c) for c in add_cues), default=-1)
        if rem < 0 and add < 0:
            continue
        (exclude if rem > add else extra).add(canon)
    return sorted(exclude), sorted(extra)


def _resolve_equipment(prompt: str) -> tuple[list[str], list[str]]:
    """Resolve equipment polarity from the prompt against the graph's Equipment
    surface forms. Thin DB-backed wrapper over `_classify_equipment`."""
    return _classify_equipment(prompt, _equipment_surface())


# Lead-ins that turn a following noun into a NAME-level exclusion — "exclude
# deadlifts", "skip burpees", "no more lunges". Distinct from the joint-avoid
# cues (those route through the clarify gate) and the equipment polarity cues:
# a captured term that resolves to a known joint / equipment / muscle / pattern
# is left to its own resolver, so what survives is an exercise-name term.
_EXCLUDE_CUES = (
    "exclude ", "excluding ", "no more ", "skip ", "skipping ", "drop ",
    "without ", "leave out ", "don't do ", "dont do ", "no ",
)
# Generic words that can follow a cue but aren't exercise names — kept out of the
# name filter so "no equipment", "no time" etc. don't become bogus substrings.
_EXCLUDE_STOP_WORDS = {
    "equipment", "kit", "gear", "weights", "weight", "time", "rest", "warmup",
    "cooldown", "session", "reps", "rep", "sets", "set", "machines", "machine",
    # compound tails so the bare "no " cue doesn't capture them ("no more X",
    # "no longer X" are handled by reaching the real noun after them).
    "more", "longer",
}


def _depluralize(tok: str) -> str:
    """Crude singular stem so a CONTAINS match on the exercise name works:
    "deadlifts" -> "deadlift", "burpees" -> "burpee". Leaves short tokens alone."""
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _resolve_exclude_terms(prompt: str, known: set[str]) -> list[str]:
    """Name-level exclusions parsed from the prompt: the noun right after an
    exclude cue, de-pluralized, dropped if it resolves to a joint / equipment /
    muscle / pattern (those have dedicated paths) or a stop-word. Returns
    lowercase name substrings for `safety.eligible`'s CONTAINS filter. Captures
    the FIRST noun after each cue only — conservative, to avoid over-reaching
    across a clause. Deterministic, no LLM."""
    pl = f" {prompt.lower()} "
    terms: set[str] = set()
    for cue in _EXCLUDE_CUES:
        start = 0
        while True:
            i = pl.find(cue, start)
            if i < 0:
                break
            start = i + len(cue)
            tail = pl[start:]
            for sep in (",", ".", ";", " but ", " and ", " or ", " with ",
                        " for ", " to ", " on "):
                tail = tail.split(sep)[0]
            words = [w.strip(".,;:") for w in tail.split() if w.strip(".,;:")]
            while words and words[0] in _EQUIP_STOPHEAD:
                words = words[1:]
            if not words:
                continue
            tok = words[0]
            if len(tok) < 3 or not tok.isalpha():
                continue
            stem = _depluralize(tok)
            if tok in known or stem in known or stem in _EXCLUDE_STOP_WORDS:
                continue
            terms.add(stem)
    return sorted(terms)


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
        eligible = safety.eligible(member_id, exclude_terms=intent.get("exclude_terms") or None,
                                   avoid_joints=state.get("avoid_joints") or None,
                                   exclude_equipment=intent.get("exclude_equipment") or None,
                                   extra_equipment=intent.get("extra_equipment") or None)
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
        filtered, filtered_summary = _filtered_out(
            member_id, state["intent"], avoid_joints=state.get("avoid_joints"))
    return {"plan": plan_dict, "provenance": provenance, "filtered": filtered,
            "filtered_summary": filtered_summary, "needs_revision": False}


def _provenance(state: GenState, plan_dict: dict) -> list[dict]:
    member_id, intent = state["member_id"], state["intent"]
    tm, tp = set(intent.get("target_muscles", [])), set(intent.get("target_patterns", []))
    excl_eq = intent.get("exclude_equipment") or None
    extra_eq = intent.get("extra_equipment") or None
    meta = {c["id"]: c for c in state["candidates"]}
    out = []
    for section in ("warmup", "main", "cooldown"):
        for p in plan_dict.get(section, []):
            c = meta.get(p["id"], {})
            because = []
            hit_m = tm & set(c.get("muscles", []))
            hit_p = tp & set(c.get("patterns", []))
            if hit_m:
                because.append(f"Targets {', '.join(sorted(hit_m))}")
            if hit_p:
                because.append(f"Matches the {', '.join(sorted(hit_p))} work you asked for")
            out.append({
                "exercise_id": p["id"], "name": p["name"],
                "chosen_because": because or ["Rounds out the session"],
                "safe_because": _safe_because(member_id, p["id"], excl_eq, extra_eq),
            })
    return out


def _safe_because(member_id: str, exercise_id: str,
                  exclude_equipment, extra_equipment) -> list[str]:
    """Derive the safety claim from the graph facts for THIS exercise, so the
    provenance reflects (and would expose a discrepancy in) the actual joint /
    pattern / equipment edges rather than asserting a hardcoded constant."""
    r = safety.safety_reasons(member_id, exercise_id,
                              exclude_equipment=exclude_equipment,
                              extra_equipment=extra_equipment)
    out = []
    # joints: only assert the no-injury claim when it actually holds (it always
    # will for a prescribed item, but the wording is built from the facts).
    if r.get("joint_ok", True):
        loaded = r.get("joints_loaded") or []
        if loaded:
            out.append(f"Works {', '.join(loaded)}, none of them injured")
        else:
            out.append("Doesn't load an injured joint")
    # pattern
    patterns = r.get("patterns") or []
    if patterns:
        out.append(f"{', '.join(patterns)} is cleared for their injuries")
    else:
        out.append("No contraindicated movement")
    # equipment
    req = r.get("required_equipment") or []
    if req:
        out.append(f"Uses {', '.join(req)}, all available")
    else:
        out.append("No equipment needed")
    return out


def _filtered_out(member_id: str, intent: dict, limit: int = 5,
                  avoid_joints: list[str] | None = None) -> list[dict]:
    """Show what the safety filter removed that the coach might have expected —
    contraindicated exercises matching the intent, with reasons + alternatives.
    Merges joint/pattern contraindications with equipment exclusions, de-duped by
    id so an exercise dropped for BOTH reasons appears once with both reasons."""
    excl_eq = intent.get("exclude_equipment") or None
    extra_eq = intent.get("extra_equipment") or None
    contra = safety.contraindicated(member_id)
    equip = safety.equipment_filtered(member_id, exclude_equipment=excl_eq,
                                      extra_equipment=extra_eq)
    # merge by id, concatenating reasons (an exercise can fail on joint AND kit)
    merged: dict[str, dict] = {}
    for c in [*contra, *equip]:
        if c["id"] in merged:
            merged[c["id"]]["reasons"] = [*merged[c["id"]]["reasons"], *c["reasons"]]
        else:
            merged[c["id"]] = {"id": c["id"], "name": c["name"],
                               "reasons": list(c["reasons"])}
    rows = list(merged.values())
    # Rank what the coach most expects to see first, so the [:limit] cap never
    # hides it: (0) equipment they EXCLUDED this session — the direct answer to
    # "no dumbbells"; (1) injury contraindications; (2) baseline missing kit.
    excl_set = {e.lower() for e in (excl_eq or [])}
    def _rank(c: dict) -> int:
        via = {v.lower() for r in c["reasons"] if r["type"] == "equipment"
               for v in r.get("via", [])}
        if excl_set & via:
            return 0
        if any(r["type"] in ("joint", "pattern") for r in c["reasons"]):
            return 1
        return 2
    rows.sort(key=lambda c: (_rank(c), c["name"]))
    tm = set(intent.get("target_muscles", []))
    relevant = rows
    if tm:
        ids = [c["id"] for c in rows]
        meta = _exercise_meta(ids)
        relevant = [c for c in rows if tm & set(meta.get(c["id"], {}).get("muscles", []))] or rows
    out = []
    for c in relevant[:limit]:
        alts = safety.alternatives(member_id, c["id"], limit=2,
                                   avoid_joints=avoid_joints,
                                   exclude_equipment=excl_eq, extra_equipment=extra_eq)
        out.append({"id": c["id"], "name": c["name"], "reasons": c["reasons"],
                    "alternatives": [a["name"] for a in alts]})
    # The list above is a capped sample; report the REAL totals so the count
    # isn't a fixed-looking 5. contra (injury) and equip are disjoint sets, so
    # the true number removed is their sum — and we split it by reason because
    # "filtered for safety" is only honest for the injury share.
    summary = {"total": len(contra) + len(equip),
               "unsafe": len(contra), "equipment": len(equip),
               "shown": len(out)}
    return out, summary


NARRATOR_SYSTEM = (
    "You are a fitness coach. In 2-3 sentences, explain this workout to the coach: "
    "what it targets, how it respects the member's journey stage, and that unsafe "
    "options were filtered out by the graph. Be concrete and warm; no preamble."
)


def narration_stream(prompt: str, result: dict):
    """Stream the coach-facing narration, decoupled from the blocking plan so the
    structured plan returns fast and prose streams after. Empty if no key; an
    on-brand cooldown line when the token budget is spent (the plan itself is
    fully built from the graph regardless)."""
    if not llm.is_available():
        if llm.budget_exhausted():
            yield ("That's a wrap — the plan above is built straight from the graph, "
                   "but we've hit the token cap so AI narration is on cooldown.")
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


# --- clarify-before-generate ------------------------------------------------
# Cues that turn a body-part mention into an avoidance ("easy on the knee",
# "without aggravating her shoulder"). A joint named WITH a cue that is NOT on
# the member's file is an ad-hoc constraint we must confirm, not silently obey
# or silently ignore.
_AVOID_CUES = (
    "avoid", "without", "aggravat", "bother", "hurt", "sore", "pain", "protect",
    "careful", "injur", "tweak", "issue", "problem", "flare", "bad ", "easy on",
    "go easy", "gentle on", "watch the", "watch her", "sensitive",
)


# Recognisable training vocabulary — if a prompt contains NONE of these (and no
# muscle/pattern/equipment from the graph), it isn't a workout request and we ask
# what to focus on rather than silently assembling a default plan from the pool.
_FITNESS_TERMS = (
    "strength", "cardio", "mobility", "flexib", "stretch", "warm", "cool", "core",
    "upper", "lower", "full body", "full-body", "push", "pull", "legs", "leg", "arm",
    "chest", "pec", "back", "shoulder", "glute", "hip", "quad", "hamstring", "calf",
    "ab", "endurance", "hiit", "interval", "recovery", "deload", "rep", "set ",
    "session", "workout", "train", "easy", "hard", "light", "heavy", "tempo",
    "circuit", "conditioning", "balance", "power", "tone", "build", "burn", "sweat",
    "gym", "band", "dumbbell", "kettlebell", "barbell", "machine", "bodyweight",
    "yoga", "pilates", "run", "row", "squat", "press", "curl", "lunge", "plank",
    "mobil", "activ", "fit", "move", "exercise",
)


def _member_name(member_id: str) -> str:
    rows = run("MATCH (m:Member {id:$id}) RETURN m.name AS name", id=member_id)
    return rows[0]["name"] if rows else "this member"


def has_fitness_signal(member_id: str, prompt: str) -> bool:
    """True if the prompt reads as a training request — a known fitness term, or a
    muscle / movement-pattern / equipment surface form from the graph."""
    pl = f" {prompt.lower()} "
    if any(t in pl for t in _FITNESS_TERMS):
        return True
    rows = run(
        "MATCH (n) WHERE n:Muscle OR n:MovementPattern OR n:Equipment "
        "RETURN coalesce(n.name,'') AS name, coalesce(n.alt_labels,[]) AS alts"
    )
    for r in rows:
        if r["name"] and f" {r['name'].lower()} " in f" {pl} ":
            return True
        if any(a and a.lower() in pl for a in r["alts"]):
            return True
    return False


def detect_clarifications(member_id: str, prompt: str,
                          avoid_joints: list[str], ignore_joints: list[str]) -> list[str]:
    """Joints the coach gestured at avoiding that AREN'T already accounted for —
    not a stored injury, not already confirmed (avoid) or waved off (ignore).
    Returns canonical joint names needing a yes/no before we generate."""
    pl = f" {prompt.lower()} "
    if not any(c in pl for c in _AVOID_CUES):
        return []
    injured = {r["name"] for r in run(
        "MATCH (:Member {id:$id})-[:HAS_INJURY]->(:Injury)-[:AFFECTS]->(j:Joint) "
        "RETURN j.name AS name", id=member_id)}
    known = injured | set(avoid_joints) | set(ignore_joints)
    # surface form (canonical name + aliases) → canonical joint name
    surface: dict[str, str] = {}
    for r in run("MATCH (j:Joint) RETURN j.name AS name, coalesce(j.alt_labels,[]) AS alts"):
        surface[r["name"].lower()] = r["name"]
        for a in r["alts"]:
            surface[a.lower()] = r["name"]
    mentioned = {canon for surf, canon in surface.items() if f" {surf} " in pl or f" {surf}" in pl}
    return sorted(j for j in mentioned if j not in known)


_EQUIP_NOUN_HINTS = (
    "cane", "canes", "stool", "block", "blocks", "ball", "rope", "ring", "rings",
    "strap", "straps", "bar", "bars", "wheel", "roller", "sled", "rack", "bench",
    "step", "box", "mat", "wall", "trx", "sandbag", "weight", "weights", "plate",
    "plates", "disc", "wedge", "parallette", "parallettes", "loop", "pad", "chair",
)
# Articles/possessives we strip from the front of a candidate kit phrase.
_EQUIP_STOPHEAD = ("the", "a", "an", "his", "her", "their", "some", "any", "my",
                   "our", "this", "that", "these", "those")


def detect_equipment_clarifications(prompt: str, exclude_equipment: list[str],
                                    extra_equipment: list[str]) -> list[str]:
    """Open-vocabulary inclusion gate: a coach saying a member HAS a piece of kit
    that we can't resolve to any Equipment node (e.g. "handstand canes", "the
    stool", "yoga blocks") is a real mention we must NOT drop silently. Returns
    the unresolved candidate terms following an addition cue so the coach can
    confirm them as available this session. Resolved kit (handled by the planner)
    and already-confirmed overrides are excluded. Deterministic, no LLM."""
    pl = f" {prompt.lower()} "
    if not any(c in pl for c in _EQUIP_ADDITION_CUES):
        return []
    surface = _equipment_surface()
    known_terms = set(surface.keys()) | {v.lower() for v in surface.values()}
    confirmed = {e.lower() for e in (*exclude_equipment, *extra_equipment)}
    unresolved: list[str] = []
    seen: set[str] = set()
    for cue in _EQUIP_ADDITION_CUES:
        start = 0
        while True:
            i = pl.find(cue, start)
            if i < 0:
                break
            start = i + len(cue)
            tail = pl[start:]
            # candidate phrase = words after the cue up to clause punctuation
            for sep in (",", ".", ";", " and ", " but ", " for ", " to "):
                tail = tail.split(sep)[0]
            words = [w for w in tail.split() if w]
            # drop a leading article/possessive ("the stool" -> "stool")
            while words and words[0] in _EQUIP_STOPHEAD:
                words = words[1:]
            if not words:
                continue
            # a candidate is kit-like only if its HEAD or LAST word is a kit noun;
            # this keeps it open-vocab (we don't enumerate kit) without flagging
            # muscle/pattern/verb phrases like "access to legs" or "got tired".
            head, last = words[0], words[-1]
            if head not in _EQUIP_NOUN_HINTS and last not in _EQUIP_NOUN_HINTS:
                continue
            # keep at most the trailing two words as the noun phrase
            cand = " ".join(words[-2:] if len(words) >= 2 else words).strip()
            if cand in known_terms or cand in confirmed or cand in seen:
                continue
            # if the head noun itself is known kit, the planner already resolved it —
            # don't re-ask (e.g. "has a bench" where "bench" is a real Equipment node)
            if last in known_terms:
                continue
            seen.add(cand)
            unresolved.append(cand)
    return unresolved


def run_generation(member_id: str, prompt: str, time_minutes: int = 45,
                   exclude_terms: list[str] | None = None,
                   avoid_joints: list[str] | None = None,
                   ignore_joints: list[str] | None = None,
                   exclude_equipment: list[str] | None = None,
                   extra_equipment: list[str] | None = None) -> tuple[dict, list[dict]]:
    avoid_joints, ignore_joints = avoid_joints or [], ignore_joints or []
    exclude_equipment, extra_equipment = exclude_equipment or [], extra_equipment or []
    trace = Trace()
    # Gate: an unrecognised avoidance constraint goes back to the coach instead of
    # generating on an assumption. Safety stays deterministic — the graph decides
    # what's ambiguous, the coach decides the answer.
    with trace.step("agent", "clarify_gate"):
        in_scope = has_fitness_signal(member_id, prompt)
        todo = detect_clarifications(member_id, prompt, avoid_joints, ignore_joints)
        equip_todo = detect_equipment_clarifications(prompt, exclude_equipment, extra_equipment)
        trace.add("decision", "fitness request?", in_scope=in_scope)
        trace.add("decision", "ambiguous avoidance?", needs=todo)
        trace.add("decision", "unknown equipment mention?", needs=equip_todo)
    # Off-topic / empty prompt → ask what to train rather than inventing a default.
    if not in_scope:
        name = _member_name(member_id)
        return ({
            "member_id": member_id,
            "clarification": {
                "joints": [],   # not an avoidance question — no yes/no, just guidance
                "scope": True,
                "questions": [
                    f"I want to program the right session for {name} — what should it "
                    "focus on? Try a muscle group (e.g. legs, back), a style (strength, "
                    "mobility, conditioning), or just say \"balanced full-body\"."
                ],
            },
        }, trace.as_list())
    if todo:
        name = _member_name(member_id)
        return ({
            "member_id": member_id,
            "clarification": {
                "kind": "joint",   # added for symmetry; existing fields kept intact
                "joints": todo,
                "questions": [
                    f"You mentioned the {j}, but {name} has no {j} injury on file. "
                    f"Should I avoid loading the {j} this session?"
                    for j in todo
                ],
            },
        }, trace.as_list())
    # Open-vocabulary inclusion gate: a real kit mention we can't resolve is
    # surfaced to the coach, never silently dropped.
    if equip_todo:
        name = _member_name(member_id)
        return ({
            "member_id": member_id,
            "clarification": {
                "kind": "equipment",
                "equipment": equip_todo,
                "questions": [
                    f"You mentioned '{t}', which isn't in {name}'s equipment. "
                    "Should I treat it as available this session?"
                    for t in equip_todo
                ],
            },
        }, trace.as_list())
    init: GenState = {
        "member_id": member_id, "prompt": prompt, "time_minutes": time_minutes,
        "exclude_terms": exclude_terms or [], "avoid_joints": avoid_joints,
        "exclude_equipment": exclude_equipment, "extra_equipment": extra_equipment,
        "trace": trace, "revisions": 0,
    }
    final = GRAPH.invoke(init)
    result = {
        "member_id": member_id,
        "intent": final["intent"],
        "plan": final["plan"],
        "provenance": final.get("provenance", []),
        "filtered_out": final.get("filtered", []),
        "filtered_summary": final.get("filtered_summary", {}),
        "journey_stage": final["journey"].get("journey_stage", "unknown"),
        "narration": final.get("narration", ""),
        "degraded": final.get("degraded", False),
        # The full graph-safe candidate pool for THIS session — every exercise
        # that passed injury/equipment/avoidance filtering. The coach may add
        # only from here, so manual customization can never insert something
        # contraindicated (safety stays deterministic even under hand-edits).
        "safe_pool": [
            {"id": c["id"], "name": c["name"],
             "pattern": (c.get("patterns") or [""])[0],
             "muscles": c.get("muscles", [])}
            for c in final.get("candidates", [])
        ],
    }
    return result, trace.as_list()
