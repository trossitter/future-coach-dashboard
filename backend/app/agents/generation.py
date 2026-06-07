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
    substitutions: list[dict]
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
            "sets": sets, "reps": reps, "rest_seconds": rest,
            "down_rank": bool(c.get("down_rank"))}


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
            # Joint-loaders are kept but penalised so they sort to the bottom and
            # are only chosen when the safe pool is thin. `down_rank` rides along
            # (from safety.eligible) onto the candidate via the **e spread.
            penalty = 0.5 if e.get("down_rank") else 0.0
            cands.append({**e, **m, "score": round(sem.get(e["id"], 0.0) + boost - penalty, 3)})
        cands.sort(key=lambda c: c["score"], reverse=True)
        trace.add("tool", "safety.eligible (graph)", eligible=len(safe_ids))
        trace.add("tool", "vector.search", ranked=len(sem))
    return {"candidates": cands, "safe_ids": safe_ids}


# Score bump applied to a substitute candidate so deterministic assembly (and the
# top-of-list the LLM sees) reliably places it. Large enough to dominate semantic
# score, so the safe swap actually lands IN the plan rather than just being noted.
_SUBSTITUTE_BOOST = 10.0


def _substitutions(member_id: str, intent: dict, candidates: list[dict],
                   *, avoid_joints: list[str] | None = None) -> list[dict]:
    """Auto-substitution: for each EQUIPMENT-filtered exercise that matches the
    coach's intent (overlaps target muscles/patterns), find the best SAFE
    same-pattern/muscle alternative that is ALREADY in the safe candidate pool, so
    it can be placed IN the plan as an explicit swap (PRD: "no barbell → drop
    barbell-only exercises and find equivalent alternatives").

    Safety stays deterministic: the dropped set comes from `safety.equipment_filtered`
    (already injury-safe), and a substitute is only ever taken from the eligible
    candidate pool via `safety.alternatives` (which honours the same session
    constraints), so a swap is never itself contraindicated or excluded. Returns
    dropped→substitute records; one substitute is never reused for two drops."""
    tm = set(intent.get("target_muscles", []))
    tp = set(intent.get("target_patterns", []))
    if not (tm or tp):
        return []
    excl_eq = intent.get("exclude_equipment") or None
    extra_eq = intent.get("extra_equipment") or None
    pool = {c["id"] for c in candidates}        # the safe candidate pool, by id
    dropped = safety.equipment_filtered(member_id, exclude_equipment=excl_eq,
                                        extra_equipment=extra_eq)
    if not dropped:
        return []
    meta = _exercise_meta([d["id"] for d in dropped])
    out: list[dict] = []
    used_subs: set[str] = set()
    for d in sorted(dropped, key=lambda c: c["name"]):
        m = meta.get(d["id"], {})
        if not (tm & set(m.get("muscles", [])) or tp & set(m.get("patterns", []))):
            continue   # not what the coach asked for — don't substitute it
        alts = safety.alternatives(member_id, d["id"], limit=8,
                                   avoid_joints=avoid_joints,
                                   exclude_equipment=excl_eq, extra_equipment=extra_eq)
        # first SAFE alternative that's actually in the eligible candidate pool
        # (so it respects dislikes / name-exclusions too) and not already claimed.
        sub = next((a for a in alts
                    if a["id"] in pool and a["id"] not in used_subs), None)
        if not sub:
            continue
        used_subs.add(sub["id"])
        via = sorted({v for r in d["reasons"] if r["type"] == "equipment"
                      for v in r.get("via", [])})
        out.append({
            "dropped": d["name"], "dropped_id": d["id"],
            "substitute": sub["name"], "substitute_id": sub["id"],
            "reason": f"needs {', '.join(via)}" if via else "needs unavailable equipment",
        })
    return out


def assemble(state: GenState) -> dict:
    trace = state["trace"]
    counts = _counts(state["time_minutes"])
    stage = state["journey"].get("journey_stage", "")
    # Auto-substitution: bias assembly so a safe swap for an equipment-dropped
    # exercise lands IN the plan. Substitutes are taken ONLY from the safe pool, so
    # boosting their score can never introduce something contraindicated.
    subs = _substitutions(state["member_id"], state["intent"], state["candidates"],
                          avoid_joints=state.get("avoid_joints"))
    sub_by_id = {s["substitute_id"]: s for s in subs}
    cands = state["candidates"]
    if sub_by_id:
        cands = [{**c, "score": round(c.get("score", 0.0) + _SUBSTITUTE_BOOST, 3)}
                 if c["id"] in sub_by_id else c for c in cands]
        cands.sort(key=lambda c: c["score"], reverse=True)
    with trace.step("agent", "assembler"):
        plan_dict, degraded = None, False
        if state.get("force_deterministic"):
            plan_dict = _deterministic_plan(cands, counts, stage)
            trace.add("critic", "rebuilt deterministically")
        elif llm.is_available():
            plan_dict = _llm_plan(state["prompt"], state["intent"], state["journey"],
                                  cands, counts)
        if plan_dict is None:
            plan_dict = _deterministic_plan(cands, counts, stage)
            degraded = not llm.is_available()
        # Auto-substitution first (it may PLACE new items), then stamp down_rank so
        # every plan item — including any placed substitute — carries the flag.
        if sub_by_id:
            _apply_substitutes(plan_dict, sub_by_id, cands, counts, stage)
        if subs:
            trace.add("decision", "auto-substitution",
                      swaps=[f"{s['dropped']} → {s['substitute']}" for s in subs])
        # Stamp the graph-derived down_rank flag onto every plan item from the
        # candidate pool, so the LLM path (whose items lack it) badges identically
        # to the deterministic path. Authoritative source: safety.eligible.
        dr = {c["id"]: bool(c.get("down_rank")) for c in state["candidates"]}
        for section in ("warmup", "main", "cooldown"):
            for p in plan_dict.get(section, []):
                p["down_rank"] = dr.get(p["id"], False)
    return {"plan": plan_dict, "degraded": degraded, "substitutions": subs}


def _apply_substitutes(plan_dict: dict, sub_by_id: dict[str, dict],
                       cands: list[dict], counts: dict, stage: str) -> None:
    """Tag any placed substitute with `substitute_for`, and place any not-yet-placed
    substitute if its section has room (best-effort for the LLM path; the score
    boost already lands them deterministically). Never displaces an existing item —
    a swap that doesn't fit is still reported in `substitutions`."""
    placed = {p["id"] for s in ("warmup", "main", "cooldown")
              for p in plan_dict.get(s, [])}
    # tag the items already in the plan
    for section in ("warmup", "main", "cooldown"):
        for p in plan_dict.get(section, []):
            s = sub_by_id.get(p["id"])
            if s:
                p["substitute_for"] = s["dropped"]
    # place any substitute that didn't make it, if there's room in its section
    cand_by_id = {c["id"]: c for c in cands}
    for sid, s in sub_by_id.items():
        if sid in placed:
            continue
        c = cand_by_id.get(sid)
        if not c:
            continue
        section = _bucket(c.get("patterns", []))
        target = plan_dict.setdefault(section, [])
        if len(target) < counts.get(section, 0):
            item = _prescribe(c, section, stage)
            item["substitute_for"] = s["dropped"]
            target.append(item)
            placed.add(sid)


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
            safe_because = _safe_because(member_id, p["id"], excl_eq, extra_eq)
            entry = {
                "exercise_id": p["id"], "name": p["name"],
                "chosen_because": because or ["Rounds out the session"],
                "safe_because": safe_because,
            }
            if c.get("down_rank") or p.get("down_rank"):
                entry["down_rank"] = True
                sr = safety.safety_reasons(member_id, p["id"],
                                           exclude_equipment=excl_eq,
                                           extra_equipment=extra_eq)
                hit_j = sorted(set(sr.get("joints_loaded", [])) &
                               set(sr.get("injured_joints", [])))
                joint = hit_j[0] if hit_j else "an injured joint"
                safe_because.append(f"Loads the {joint} — included sparingly")
            out.append(entry)
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
    # Joint-only contraindications are no longer "filtered for safety" — they're
    # kept in the plan and down-ranked. Only PATTERN contraindications remain a
    # hard exclude, so keep just those (strip joint reasons too, so an item that's
    # excluded on pattern reports only the reason that actually removed it).
    contra = []
    for c in safety.contraindicated(member_id):
        pat = [r for r in c["reasons"] if r["type"] == "pattern"]
        if pat:
            contra.append({**c, "reasons": pat})
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
    "You are the coach, sending the member a quick note with their workout — like a "
    "text before they train. VOICE: first person ('I've ...'), to the member as "
    "'you/your', never third person. LENGTH: 1-2 sentences, warm and human — a little "
    "encouragement is welcome; don't sound like a robot. "
    "Tell them what's IN STORE: the focus and feel of the session (e.g. 'upper-body "
    "push and some core work'). You may name a highlight move or two, but do NOT "
    "roll-call every exercise — the plan is right there beneath the note. If you "
    "adjusted for their safety, work in ONE warm clause about it (e.g. 'and I kept "
    "the load off your knee by swapping out the lunges and step-ups'). "
    "GROUNDING (the graph owns the facts): name ONLY exercises present in the JSON — "
    "never invent one, and only name a removed exercise if it's in "
    "`filtered_for_safety`. No invented anatomy ('flexion', 'rotation', 'instability'). "
    "NEVER list everything that was removed. Skip empty filler ('building resilience', "
    "'respects where you are in your journey'). "
    "OUTPUT PROSE ONLY — flowing sentences. Do NOT reproduce the workout plan or its "
    "warmup/main/cooldown sections (it's shown separately), and use NO headings, NO "
    "bullet or numbered lists, NO asterisks/markdown, NO preamble."
)


def _filtered_because(f: dict) -> list[str]:
    """Human-readable, GRAPH-DERIVED reasons an exercise was filtered — built from
    the same reason records the evidence panel shows, so the narrator phrases the
    graph's actual basis rather than inventing one."""
    out: list[str] = []
    for r in f.get("reasons", []):
        via = ", ".join(r.get("via", []))
        if r["type"] == "joint":
            out.append(f"loads the {via}" if via else "loads a flagged joint")
        elif r["type"] == "pattern":
            out.append(f"contraindicated movement pattern ({via})" if via
                       else "a contraindicated movement pattern")
        elif r["type"] == "equipment":
            out.append(f"needs unavailable equipment ({via})" if via
                       else "needs unavailable equipment")
    return out


def _narration_payload(prompt: str, result: dict) -> dict:
    """The grounded fact-set handed to the narrator: the plan by section plus the
    filtered exercises WITH their graph reasons. Pure (no LLM/DB) so it's testable
    and so what the model sees is exactly what the graph produced."""
    plan_dict = result["plan"]
    names = {s: [p["name"] for p in plan_dict.get(s, [])]
             for s in ("warmup", "main", "cooldown")}
    # Only SAFETY-relevant removals are worth a brief mention to the member;
    # equipment-only exclusions (gear they don't have) are noise to them and live
    # in the coach's evidence panel, not the prose. Drop them here so the narrator
    # can't enumerate them at all.
    safety_filtered = []
    for f in result.get("filtered_out", []):
        reasons = [r for r in f.get("reasons", []) if r["type"] in ("joint", "pattern")]
        if reasons:
            safety_filtered.append({
                "name": f["name"],
                "filtered_because": _filtered_because({"reasons": reasons})})
    return {"request": prompt, "journey_stage": result["journey_stage"],
            "plan": names, "filtered_for_safety": safety_filtered}


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
    user = json.dumps(_narration_payload(prompt, result))
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


# --- requested-but-filtered acknowledgment ----------------------------------
# When a coach explicitly NAMES an exercise the safety filter then removes, we
# don't silently drop it: we say so, with the reason + a safe swap. This never
# overrides safety (the exercise stays out) — it just explains, the way the
# clarify gate refuses to guess.

# Equipment/modifier words in exercise names that don't, on their own, signal a
# coach naming a movement ("dumbbell", "alternating", "overhead"). Kept out so
# only movement-distinctive tokens (squat, lunge, jump, press, plank…) match.
_NAME_TOKEN_STOP = {
    "dumbbell", "barbell", "kettlebell", "band", "cable", "machine", "bench",
    "bosu", "ball", "with", "from", "into", "onto", "alternating", "assisted",
    "racked", "anchored", "neutral", "grip", "standing", "seated", "kneeling",
    "half", "single", "double", "front", "back", "side", "low", "high", "loop",
    "resistance", "weighted", "bodyweight", "wall", "floor", "mat", "med",
    "reverse", "forward", "lateral", "overhead", "incline", "decline", "close",
    "wide", "this", "that", "your", "their", "drive",
}


def _exercise_name_index() -> tuple[dict[str, set[str]], dict[str, str]]:
    """(token/full-name -> {exercise ids}, id -> name). Tokens are de-pluralized
    movement words from each exercise name; equipment/modifier words are dropped."""
    idx: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for r in run("MATCH (e:Exercise) RETURN e.id AS id, e.name AS name"):
        names[r["id"]] = r["name"]
        nm = r["name"].lower()
        idx.setdefault(nm, set()).add(r["id"])  # whole name (e.g. "jumping jacks")
        for raw in nm.replace("-", " ").replace("(", " ").replace(")", " ").split():
            w = _depluralize(raw)
            if len(w) >= 4 and w not in _NAME_TOKEN_STOP:
                idx.setdefault(w, set()).add(r["id"])
    return idx, names


def _required_equipment(ex_id: str) -> set[str]:
    rows = run("MATCH (e:Exercise {id:$id})-[:REQUIRES]->(eq:Equipment) "
               "RETURN collect(DISTINCT eq.name) AS r", id=ex_id)
    return set(rows[0]["r"]) if rows else set()


def _loads_avoided_joint(ex_id: str, avoid_joints: list[str]) -> list[str]:
    if not avoid_joints:
        return []
    rows = run(
        """
        MATCH (ex:Exercise {id:$exid})-[:LOADS]->(loaded:Joint)
        MATCH (aj:Joint) WHERE aj.name IN $avoid
          AND ((loaded)-[:PART_OF*0..]->(aj) OR (aj)-[:PART_OF*0..]->(loaded))
        RETURN collect(DISTINCT aj.name) AS hit
        """,
        exid=ex_id, avoid=avoid_joints,
    )
    return rows[0]["hit"] if rows else []


def _why_one(member_id: str, ex_id: str, intent: dict, avoid_joints: list[str]) -> str | None:
    """Short, graph-derived reason a NAMED exercise was filtered (None => it's
    actually eligible). Session-aware: stored injury, session avoid-joint, then
    equipment (session override then baseline)."""
    skipped = safety.why_skipped(member_id, ex_id)
    joint = next((r for r in skipped if r["reason"] == "injury_joint"), None)
    if joint:
        return f"it stresses their {joint.get('detail', 'injured joint')}"
    patt = next((r for r in skipped if r["reason"] == "injury_pattern"), None)
    if patt:
        return f"it's {patt['via']} work, off-limits for their {patt.get('detail', 'injury')}"
    hit = _loads_avoided_joint(ex_id, avoid_joints)
    if hit:
        return f"it loads the {', '.join(hit)} you're avoiding this session"
    excl = set(intent.get("exclude_equipment") or [])
    if excl:
        need = excl & _required_equipment(ex_id)
        if need:
            return f"it needs {', '.join(sorted(need))}, set aside this session"
    equip = next((r for r in skipped if r["reason"] == "equipment"), None)
    if equip:
        return f"it needs {equip['via']}, which isn't available"
    return None


def _requested_but_filtered(member_id: str, prompt: str, intent: dict,
                            avoid_joints: list[str], safe_ids: list[str]) -> list[dict]:
    """Exercises the coach named in the prompt that the graph filtered out, each
    with the reason + a safe alternative. A named movement with ANY eligible match
    is treated as satisfied (no nag). Capped, deterministic, no LLM."""
    pl = f" {prompt.lower()} "
    safe = set(safe_ids)
    idx, names = _exercise_name_index()
    # de-pluralized prompt words for whole-word matching, so a single-word token
    # like "jump" doesn't match "jumping" (a different movement) via prefix.
    pwords = {_depluralize(w.strip(".,;:!?()'\"")) for w in pl.split()}
    named = [t for t in idx
             if (" " in t and t in pl) or (" " not in t and t in pwords)]
    out: dict[str, dict] = {}
    # longer (full-name) tokens first, so we report the precise exercise and skip
    # the broader single-word group that overlaps it.
    for t in sorted(named, key=len, reverse=True):
        ids = idx[t]
        if ids & safe:           # the coach's request is met by an eligible match
            continue
        if ids & set(out):       # already reported something from this movement
            continue
        rep = sorted(ids, key=lambda i: names.get(i, ""))[0]
        reason = _why_one(member_id, rep, intent, avoid_joints)
        if not reason:           # not actually filtered — leave it alone
            continue
        alts = safety.alternatives(
            member_id, rep, limit=1, avoid_joints=avoid_joints or None,
            exclude_equipment=intent.get("exclude_equipment") or None,
            extra_equipment=intent.get("extra_equipment") or None)
        out[rep] = {"name": names.get(rep, rep), "reason": reason,
                    "alternative": alts[0]["name"] if alts else None}
    return list(out.values())[:3]


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
        # Auto-substitution: equipment-dropped exercises matching the coach's
        # intent, each paired with a SAFE same-pattern swap that was placed IN the
        # plan (the prescribed item carries `substitute_for`). Separate result key —
        # the per-item provenance loop is owned by another agent.
        "substitutions": final.get("substitutions", []),
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
             "muscles": c.get("muscles", []),
             "down_rank": bool(c.get("down_rank"))}
            for c in final.get("candidates", [])
        ],
        # Exercises the coach explicitly named that the graph filtered out — shown
        # with reason + a safe swap, instead of silently dropping the request.
        "requested_unavailable": _requested_but_filtered(
            member_id, prompt, final["intent"], avoid_joints, final.get("safe_ids", [])),
    }
    return result, trace.as_list()
