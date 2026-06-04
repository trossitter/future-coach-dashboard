"""Longitudinal reasoning over KG2 — adherence, sleep, weight, journey stage.

The PRD asks us to consider "where the member is in their journey" and treat
longitudinal data types differently. This module computes that from the graph
so both surfaces reason over trends rather than a single snapshot: the
generation Planner uses `journey_stage` to bias volume/intensity, and the
copilot uses the trends to answer "how's adherence?" / "what changed?".
"""
from __future__ import annotations

from .db import run

# journey stage -> how the workout generator should bias the plan
STAGE_BIAS = {
    "onboarding": "Cold start: no history. Keep it foundational, conservative, "
                  "low-volume; prioritise confidence and technique.",
    "at_risk": "Adherence is sliding / churn elevated. Re-engage: shorter, "
               "achievable session; protect the streak over pushing load.",
    "progressing": "Consistent and recovered. A measured progression in load or "
                   "volume is appropriate.",
    "maintaining": "Steady. Hold the current stimulus; adjust to today's "
                   "constraints.",
}


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "unknown"
    delta = values[-1] - values[0]
    if delta <= -10:
        return "declining"
    if delta >= 10:
        return "improving"
    return "steady"


def summary(member_id: str) -> dict:
    weeks = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_ADHERENCE_WEEK]->(w:AdherenceWeek)
        RETURN w.week_of AS week_of, w.pct AS pct ORDER BY w.week_of
        """,
        id=member_id,
    )
    prof = run(
        """
        MATCH (m:Member {id: $id})
        OPTIONAL MATCH (m)-[:PERFORMED]->(s:Session)
        OPTIONAL MATCH (m)-[:HAS_BRIEF]->(b:CoachBrief)
        RETURN m.member_since AS since, m.adherence_trend AS stated_trend,
               m.sleep_hours_last_7_days AS sleep,
               count(DISTINCT s) AS sessions,
               head(collect(DISTINCT b.churn_level)) AS churn_level
        """,
        id=member_id,
    )
    if not prof:
        return {"member_id": member_id, "found": False}
    p = prof[0]

    pcts = [w["pct"] for w in weeks]
    adherence_trend = _trend(pcts)
    sleep = p["sleep"] or []
    avg_sleep = round(sum(sleep) / len(sleep), 1) if sleep else None

    has_history = bool(pcts) or (p["sessions"] or 0) > 0
    churn = (p["churn_level"] or "").lower()
    if not has_history:
        stage = "onboarding"
    elif churn == "elevated" or (adherence_trend == "declining" and pcts and pcts[-1] <= 50):
        stage = "at_risk"
    elif pcts and min(pcts[-3:]) >= 90:
        stage = "progressing"
    else:
        stage = "maintaining"

    oura_rows = run(
        """
        MATCH (m:Member {id: $id})-[:HAS_OURA_READING]->(o:OuraReading)
        RETURN o.date AS date, o.sleep_score AS sleep_score,
               o.readiness_score AS readiness ORDER BY o.date
        """,
        id=member_id,
    )
    oura = None
    if oura_rows:
        s = [r["sleep_score"] for r in oura_rows if r["sleep_score"] is not None]
        oura = {
            "days": len(oura_rows),
            "latest_sleep_score": s[-1] if s else None,
            "avg_sleep_score": round(sum(s) / len(s)) if s else None,
            "sleep_score_trend": _trend([float(x) for x in s]),
            "latest_readiness": oura_rows[-1]["readiness"],
        }

    return {
        "member_id": member_id,
        "found": True,
        "journey_stage": stage,
        "oura": oura,
        "generation_bias": STAGE_BIAS[stage],
        "adherence": {"weeks": weeks, "latest_pct": pcts[-1] if pcts else None,
                      "trend": adherence_trend, "stated_trend": p["stated_trend"]},
        "sessions_logged": p["sessions"],
        "avg_sleep_hours": avg_sleep,
        "churn_level": p["churn_level"],
        "member_since": p["since"],
    }
