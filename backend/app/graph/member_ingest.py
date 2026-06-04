"""KG2 — Member Context graph ingestion.

Turns the rich member-context.json into nodes/edges, cross-linking into KG1
(Injury -> Joint, Member -> Equipment). Tolerant of missing fields, so the same
function ingests the provided rich member and the thinner synthetic extras.
Idempotent (MERGE on stable keys) so re-running /ingest is safe.

Schema (KG2):
  (:Member)-[:HAS_GOAL]->(:Goal)
  (:Member)-[:HAS_INJURY]->(:Injury)-[:AFFECTS]->(:Joint)        cross-link -> KG1
  (:Member)-[:HAS_ACCESS_TO]->(:Equipment)                       cross-link -> KG1
  (:Member)-[:PERFORMED]->(:Session)
  (:Member)-[:HAS_ADHERENCE_WEEK]->(:AdherenceWeek)
  (:Member)-[:HAS_WEIGHT_SAMPLE]->(:WeightSample)
  (:Member)-[:HAS_LAB]->(:Lab:BloodPanel | :Lab:DexaScan)
  (:Member)-[:SAID]->(:ChatMessage)                              embedded for retrieval
  (:Member)-[:HAS_BRIEF]->(:CoachBrief)-[:HAS_TASK]->(:MorningTask)
Scalar biomarkers + preferences live as Member properties.
"""
from __future__ import annotations

from ..db import run
from ..embeddings import embed


def ingest_member_context(member: dict) -> str:
    p = member["profile"]
    mid = p["id"]
    prefs = member.get("preferences", {})
    bio = member.get("biomarkers", {})

    run(
        """
        MERGE (m:Member {id: $id})
        SET m.name = $name, m.age = $age, m.sex = $sex,
            m.height_cm = $height_cm, m.weight_kg = $weight_kg,
            m.timezone = $timezone, m.member_since = $member_since,
            m.coach_id = $coach_id, m.tier = $tier,
            m.preferred_session_minutes = $psm,
            m.training_days_per_week = $tdpw,
            m.preferred_days = $pdays, m.dislikes = $dislikes,
            m.preference_notes = $pnotes,
            m.resting_hr_bpm = $rhr, m.hrv_ms = $hrv,
            m.sleep_hours_last_7_days = $sleep,
            m.synthetic = true
        """,
        id=mid, name=p.get("name"), age=p.get("age"), sex=p.get("sex"),
        height_cm=p.get("height_cm"), weight_kg=p.get("weight_kg"),
        timezone=p.get("timezone"), member_since=p.get("member_since"),
        coach_id=p.get("coach_id"), tier=p.get("tier"),
        psm=prefs.get("preferred_session_minutes"),
        tdpw=prefs.get("training_days_per_week"),
        pdays=prefs.get("preferred_days", []),
        dislikes=prefs.get("dislikes", []),
        pnotes=prefs.get("notes"),
        rhr=bio.get("resting_hr_bpm"), hrv=bio.get("hrv_ms"),
        sleep=bio.get("sleep_hours_last_7_days", []),
    )

    run(
        """
        MATCH (m:Member {id: $id})
        UNWIND $rows AS g
        MERGE (goal:Goal {id: g.id})
        SET goal.text = g.text, goal.priority = g.priority,
            goal.target_date = g.target_date
        MERGE (m)-[:HAS_GOAL]->(goal)
        """,
        id=mid, rows=member.get("goals", []),
    )

    run(
        """
        MATCH (m:Member {id: $id})
        UNWIND $rows AS eq
        MERGE (e:Equipment {name: eq})
        MERGE (m)-[:HAS_ACCESS_TO]->(e)
        """,
        id=mid, rows=member.get("equipment_available", []),
    )

    for inj in member.get("injuries", []):
        run(
            """
            MATCH (m:Member {id: $id})
            MERGE (i:Injury {id: $iid})
            SET i.region = $region, i.joint = $joint, i.status = $status,
                i.severity = $severity, i.since = $since, i.notes = $notes,
                i.snomedct_hint = $hint
            MERGE (m)-[:HAS_INJURY]->(i)
            WITH i
            FOREACH (jn IN $joints |
                MERGE (j:Joint {name: jn}) MERGE (i)-[:AFFECTS]->(j))
            """,
            id=mid, iid=inj["id"], region=inj.get("region"), joint=inj.get("joint"),
            status=inj.get("status"), severity=inj.get("severity"),
            since=inj.get("since"), notes=inj.get("notes"),
            hint=inj.get("snomedct_hint"),
            joints=[inj["joint"]] if inj.get("joint") else [],
        )

    run(
        """
        MATCH (m:Member {id: $id})
        UNWIND $rows AS s
        MERGE (sess:Session {member_id: $id, date: s.date})
        SET sess.title = s.title, sess.planned = s.planned,
            sess.completed = s.completed, sess.duration_min = s.duration_min,
            sess.rpe = s.rpe, sess.exercises = s.exercises
        MERGE (m)-[:PERFORMED]->(sess)
        """,
        id=mid, rows=member.get("workout_history", []),
    )

    adh = member.get("adherence", {})
    run(
        """
        MATCH (m:Member {id: $id})
        SET m.adherence_trend = $trend
        WITH m
        UNWIND $weeks AS w
        MERGE (aw:AdherenceWeek {member_id: $id, week_of: w.week_of})
        SET aw.pct = w.pct
        MERGE (m)-[:HAS_ADHERENCE_WEEK]->(aw)
        """,
        id=mid, trend=adh.get("trend"),
        weeks=adh.get("weekly_completion_pct", []),
    )

    run(
        """
        MATCH (m:Member {id: $id})
        UNWIND $rows AS w
        MERGE (ws:WeightSample {member_id: $id, date: w.date})
        SET ws.kg = w.kg
        MERGE (m)-[:HAS_WEIGHT_SAMPLE]->(ws)
        """,
        id=mid, rows=bio.get("weight_trend_kg", []),
    )

    labs = member.get("labs", {})
    for sublabel, payload in (("BloodPanel", labs.get("blood_panel")),
                              ("DexaScan", labs.get("dexa_scan"))):
        if payload:
            run(
                f"""
                MATCH (m:Member {{id: $id}})
                MERGE (l:Lab:{sublabel} {{member_id: $id}})
                SET l += $props
                MERGE (m)-[:HAS_LAB]->(l)
                """,
                id=mid, props=payload,
            )

    oura = (member.get("wearables") or {}).get("oura_ring")
    if oura:
        run(
            """
            MATCH (m:Member {id: $id})
            SET m.oura_device = $device, m.oura_last_synced = $synced
            WITH m
            UNWIND $daily AS d
            MERGE (o:OuraReading {member_id: $id, date: d.date})
            SET o += d
            MERGE (m)-[:HAS_OURA_READING]->(o)
            """,
            id=mid, device=oura.get("device"), synced=oura.get("last_synced"),
            daily=oura.get("daily", []),
        )

    chats = member.get("chat_history", [])
    if chats:
        vecs = embed([c["text"] for c in chats])
        rows = [
            {
                "ts": c["ts"], "from": c.get("from"), "text": c["text"], "vec": v,
                "has_attachment": bool(c.get("attachments")),
                "attachment_captions": [a.get("caption") for a in c.get("attachments", [])],
            }
            for c, v in zip(chats, vecs)
        ]
        run(
            """
            MATCH (m:Member {id: $id})
            UNWIND $rows AS c
            MERGE (msg:ChatMessage {member_id: $id, ts: c.ts})
            SET msg.from = c.from, msg.text = c.text, msg.embedding = c.vec,
                msg.has_attachment = c.has_attachment,
                msg.attachment_captions = c.attachment_captions
            MERGE (m)-[:SAID]->(msg)
            """,
            id=mid, rows=rows,
        )

    cb = member.get("coach_brief")
    if cb:
        churn = cb.get("churn_risk", {})
        run(
            """
            MATCH (m:Member {id: $id})
            MERGE (b:CoachBrief {member_id: $id, generated_for: $gf})
            SET b.churn_level = $level, b.churn_reasons = $reasons
            MERGE (m)-[:HAS_BRIEF]->(b)
            WITH b
            UNWIND $tasks AS t
            MERGE (mt:MorningTask {member_id: $id, type: t.type, text: t.text})
            MERGE (b)-[:HAS_TASK]->(mt)
            """,
            id=mid, gf=cb.get("generated_for"),
            level=churn.get("level"), reasons=churn.get("reasons", []),
            tasks=cb.get("morning_tasks", []),
        )

    return mid
