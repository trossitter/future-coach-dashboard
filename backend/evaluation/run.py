"""Offline evaluation pipeline — retrieval relevance, recommendation quality,
and the safety invariant, across all members.

Run:  docker compose exec backend python -m evaluation.run

This is the production-evaluation harness in miniature: it scores the parts that
must hold (concept resolution, no-unsafe-recommendation) on the synthetic
members, and is the place new labeled cases would be added.
"""
from __future__ import annotations

from app import resolver, safety
from app.agents.generation import run_generation
from app.db import run
from app.graph.ingest import ingest_all

# Labeled resolution cases (free text → expected canonical concept).
RESOLVER_CASES = [
    ("pecs", "Muscle", "chest"),
    ("delts", "Muscle", "deltoids"),
    ("hammies", "Muscle", "hamstrings"),
    ("tricpes", "Muscle", "triceps"),       # typo
    ("quads", "Muscle", "quads"),
    ("abs", "Muscle", "core"),
    ("neck", "Joint", "cervical spine"),
    ("my knee has been bugging me", "Joint", "knee"),
    ("kettlebell", "Equipment", "Kettlebell"),
]

# Retrieval relevance: query → a token expected in a top-k exercise name.
RELEVANCE_CASES = [
    ("chest fly isolation", "chest"),
    ("explosive jumping for legs", "jump"),
    ("pull up", "pull"),
]

GENERATION_CASES = [
    ("mbr_01HX9JORDAN", "lower-body strength, protect the knee", 45),
    ("mbr_duncan", "upper body push day", 60),
    ("mbr_alia", "gentle full body within my limits", 30),
]


def eval_resolution() -> tuple[int, int]:
    ok = sum(resolver.resolve(t, lbl)["match"] == exp for t, lbl, exp in RESOLVER_CASES)
    return ok, len(RESOLVER_CASES)


def eval_relevance() -> tuple[int, int]:
    ok = 0
    for q, token in RELEVANCE_CASES:
        names = [h["name"].lower() for h in resolver.semantic_exercise_search(q, k=5)]
        ok += any(token in n for n in names)
    return ok, len(RELEVANCE_CASES)


def eval_safety() -> tuple[list[str], int, int]:
    members = [r["id"] for r in run("MATCH (m:Member) RETURN m.id AS id")]
    contra_violations = equip_violations = 0
    for mid in members:
        eligible = {e["id"] for e in safety.eligible(mid)}
        contra = {c["id"] for c in safety.contraindicated(mid)}
        contra_violations += len(eligible & contra)
        bad = run(
            "MATCH (e:Exercise)-[:REQUIRES]->(eq:Equipment) WHERE e.id IN $ids "
            "AND NOT (:Member {id:$mid})-[:HAS_ACCESS_TO]->(eq) "
            "RETURN count(DISTINCT e) AS n", ids=list(eligible), mid=mid)
        equip_violations += bad[0]["n"]
    return members, contra_violations, equip_violations


def eval_recommendations() -> list[tuple]:
    rows = []
    for mid, prompt, t in GENERATION_CASES:
        res, _ = run_generation(mid, prompt, t)
        ids = [p["id"] for s in ("warmup", "main", "cooldown") for p in res["plan"][s]]
        safe = {e["id"] for e in safety.eligible(mid)}
        rows.append((mid, len(ids), all(i in safe for i in ids)))
    return rows


def main() -> None:
    ingest_all()
    r_ok, r_n = eval_resolution()
    rel_ok, rel_n = eval_relevance()
    members, contra_v, equip_v = eval_safety()
    recs = eval_recommendations()

    bar = "=" * 56
    print(f"\n{bar}\nEVALUATION REPORT\n{bar}")
    print(f"\n[1] Concept-resolution accuracy   {r_ok}/{r_n}  ({100 * r_ok // r_n}%)")
    print(f"[2] Retrieval relevance @k=5      {rel_ok}/{rel_n}  ({100 * rel_ok // rel_n}%)")
    print(f"\n[3] Safety invariant across {len(members)} members")
    print(f"    contraindicated-yet-eligible : {contra_v}   (must be 0)")
    print(f"    equipment violations         : {equip_v}   (must be 0)")
    print(f"    → safety precision           : "
          f"{'1.00  PERFECT' if contra_v == 0 and equip_v == 0 else 'FAILED'}")
    print("\n[4] Recommendation quality (plan ⊆ safe set)")
    for mid, n, ok in recs:
        print(f"    {mid:18} {n:2} exercises   all-safe: {ok}")
    print(f"\n{bar}\n")


if __name__ == "__main__":
    main()
