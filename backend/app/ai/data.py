"""Query helpers that gather a user's recent fitness data into a JSON blob the
LLM can read. Used by both daily-brief generation and chat tool calls."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.variety import summarise as summarise_variety
from app.models import (
    BodyMetric,
    ChatMessage,
    DailyBrief,
    DayLog,
    Goal,
    HeartRateSample,
    HRVReading,
    ReadinessScore,
    SleepSession,
    User,
    Workout,
)


def _local(dt: datetime | None, tz) -> str | None:
    """A timestamp in the athlete's own clock.

    Serialising UTC to the model made it reason in the wrong frame: a session
    finished at 10pm on Tuesday in Boston is 02:00 Wednesday in UTC, and the
    coach quite correctly described it as "early this morning" — correct for the
    timestamp, wrong for the person reading it.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).isoformat()


def gather_recent(db: Session, user: User, days: int = 14) -> dict[str, Any]:
    """Everything the coach reads, with the empty parts left out.

    Sections with no rows are dropped rather than sent as empty lists. A key
    reading `"hrv": []` is an explicit statement that HRV is missing, and the
    model quite reasonably mentions it — which on the home screen turns into a
    daily complaint about equipment the athlete does not own. What is absent
    should simply be absent.
    """
    from app.dates import zone_for

    tz = zone_for(user)
    snap = _gather(db, user, days)
    snap["now"] = datetime.now(tz).isoformat()
    # Stated outright as well as applied, so the model has no reason to reach
    # for UTC when describing when something happened.
    snap["timezone"] = str(tz)
    snap["note"] = "All times are the athlete's local time."

    for w in snap.get("workouts", []):
        w["started_at"] = _local(w["_started_at"], tz)
        del w["_started_at"]
    for h in snap.get("hrv", []):
        h["measured_at"] = _local(h["_measured_at"], tz)
        del h["_measured_at"]
    for b in snap.get("body_metrics", []):
        b["measured_at"] = _local(b["_measured_at"], tz)
        del b["_measured_at"]
    for r in snap.get("resting_hr_recent", []):
        r["measured_at"] = _local(r["_measured_at"], tz)
        del r["_measured_at"]
    for sl in snap.get("sleep", []):
        sl["date"] = _local(sl["_started_at"], tz)[:10]
        del sl["_started_at"]
    return _drop_empty(snap)


def _drop_empty(snap: dict[str, Any]) -> dict[str, Any]:
    # `goal` is kept even when None: the coach needs to know a goal was never
    # set, which is actionable, unlike a sensor that does not exist.
    return {
        k: v for k, v in snap.items()
        if k in ("now", "window_days", "goal") or v
    }


def _gather(db: Session, user: User, days: int = 14) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return {
        "now": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "workouts": [_workout(w) for w in db.execute(
            select(Workout).where(Workout.user_id == user.id, Workout.started_at >= cutoff)
            .order_by(Workout.started_at.desc())
        ).scalars()],
        "sleep": [_sleep(s) for s in db.execute(
            select(SleepSession).where(SleepSession.user_id == user.id, SleepSession.started_at >= cutoff)
            .order_by(SleepSession.started_at.desc())
        ).scalars()],
        "hrv": [_hrv(h) for h in db.execute(
            select(HRVReading).where(HRVReading.user_id == user.id, HRVReading.measured_at >= cutoff)
            .order_by(HRVReading.measured_at.desc())
        ).scalars()],
        "readiness": [_readiness(r) for r in db.execute(
            select(ReadinessScore).where(ReadinessScore.user_id == user.id, ReadinessScore.day >= cutoff.date())
            .order_by(ReadinessScore.day.desc())
        ).scalars()],
        "body_metrics": [_body(b) for b in db.execute(
            select(BodyMetric).where(BodyMetric.user_id == user.id, BodyMetric.measured_at >= cutoff)
            .order_by(BodyMetric.measured_at.desc())
        ).scalars()],
        "resting_hr_recent": _resting_hr(db, user, cutoff),
        "goal": _goal(db, user),
        "recent_sessions": _recent_sessions(db, user, days=10),
        # Counted, not listed. The coach was given ten days of sessions and
        # repeated itself for fourteen days anyway — a list is something it can
        # read past, a count is not.
        "variety_check": summarise_variety(db, user, days=14).as_dict(),
        # What the athlete has actually said. They tell the coach things in chat
        # — a sore knee, a trip next week, that they hate rowing — and none of it
        # reached the brief, so the coach forgot everything between screens.
        "athlete_said_recently": _recent_athlete_messages(db, user, days=7),
    }


def _recent_athlete_messages(db: Session, user: User, days: int = 7) -> list[dict]:
    """The athlete's own words from chat, newest last.

    Their messages only. The coach's own replies are already reflected in what
    it prescribed, and feeding them back doubles the context to restate what it
    decided rather than what it was told.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.user_id == user.id,
            ChatMessage.role == "user",
            ChatMessage.created_at >= cutoff,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(25)
    ).scalars().all()
    return [
        {"said": m.content[:400], "when": m.created_at, "context": m.kind}
        for m in reversed(rows)
    ]


def _recent_sessions(db: Session, user: User, days: int = 10) -> list[dict]:
    """What was prescribed lately, and what the athlete actually did about it.

    Without this the coach writes every brief from a blank slate. It cannot see
    that it asked for back squats three sessions running, so it keeps asking —
    which is exactly how a plan ends up with no variety. Provider data does not
    fill the gap: a Strava strength workout is a duration and a heart rate, with
    no record of which lifts were performed.

    The logged sets and reps matter as much as the prescription. Knowing they
    did 4x6 where 3x8 was asked is what turns the next session into progression
    rather than a repeat.
    """
    from app.dates import today_for

    start = today_for(user) - timedelta(days=days)
    briefs = db.execute(
        select(DailyBrief)
        .where(DailyBrief.user_id == user.id, DailyBrief.day >= start)
        .order_by(DailyBrief.day.desc())
    ).scalars().all()
    logs = {
        entry.day: entry
        for entry in db.execute(
            select(DayLog).where(DayLog.user_id == user.id, DayLog.day >= start)
        ).scalars()
    }

    out = []
    for b in briefs:
        log = logs.get(b.day)
        done = set(log.completed_items or []) if log else set()
        actuals = (log.item_actuals or {}) if log else {}
        items = []
        for i in (b.session_items or []):
            if not isinstance(i, dict) or not i.get("id"):
                continue
            entry = {
                "text": i.get("text"),
                "prescribed_sets": i.get("sets"),
                "prescribed_reps": i.get("reps"),
                "completed": i["id"] in done,
            }
            actual = actuals.get(i["id"])
            if isinstance(actual, dict) and actual:
                entry["actually_did"] = actual
            items.append(entry)
        entry = {
            "day": b.day.isoformat(),
            "prescribed": b.training_prescription,
            "was_training_day": b.session_planned,
            "items": items,
            "athlete_notes": (log.journal if log else None),
        }
        if b.adjustment_note:
            # What they asked to change, in their words. "No barbell today" is
            # the single most useful thing to know when programming tomorrow —
            # without it the coach keeps prescribing equipment they do not have.
            entry["athlete_changed_this"] = b.adjustment_note
        out.append(entry)
    return out


def _workout(w: Workout) -> dict:
    return {
        "id": str(w.id),
        "source": w.source,
        "sport": w.sport,
        "_started_at": w.started_at,
        "duration_min": round(w.duration_s / 60, 1),
        "distance_km": round(w.distance_m / 1000, 2) if w.distance_m else None,
        "avg_hr": w.avg_hr,
        "max_hr": w.max_hr,
        "tss": w.tss,
    }


def _sleep(s: SleepSession) -> dict:
    total = sum(x for x in (s.deep_s, s.light_s, s.rem_s) if x) or None
    return {
        "id": str(s.id),
        "source": s.source,
        "_started_at": s.started_at,
        "total_min": round(total / 60, 1) if total else None,
        "deep_min": round(s.deep_s / 60, 1) if s.deep_s else None,
        "rem_min": round(s.rem_s / 60, 1) if s.rem_s else None,
        "efficiency": s.efficiency,
    }


def _hrv(h: HRVReading) -> dict:
    return {
        "source": h.source,
        "_measured_at": h.measured_at,
        "rmssd_ms": h.rmssd_ms,
        "sdnn_ms": h.sdnn_ms,
    }


def _readiness(r: ReadinessScore) -> dict:
    return {"source": r.source, "day": r.day.isoformat(), "score": r.score}


def _body(b: BodyMetric) -> dict:
    return {
        "source": b.source,
        "metric": b.metric,
        "value": b.value,
        "_measured_at": b.measured_at,
    }


def _resting_hr(db: Session, user: User, cutoff: datetime) -> list[dict]:
    rows = db.execute(
        select(HeartRateSample)
        .where(
            HeartRateSample.user_id == user.id,
            HeartRateSample.context == "rest",
            HeartRateSample.measured_at >= cutoff,
        )
        .order_by(HeartRateSample.measured_at.desc())
        .limit(50)
    ).scalars()
    return [{"_measured_at": r.measured_at, "bpm": r.bpm} for r in rows]


def _goal(db: Session, user: User) -> dict | None:
    g = db.execute(select(Goal).where(Goal.user_id == user.id)).scalar_one_or_none()
    if g is None:
        return None
    return {
        "kind": g.kind,
        "sport": g.sport,
        "target_date": g.target_date.isoformat() if g.target_date else None,
        "description": g.description,
        "params": g.params,
    }


def user_profile(user: User, goal: dict | None) -> dict:
    return {
        "display_name": user.display_name,
        "timezone": user.timezone,
        "goal": goal,
    }
