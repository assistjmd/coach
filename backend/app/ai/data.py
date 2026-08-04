"""Query helpers that gather a user's recent fitness data into a JSON blob the
LLM can read. Used by both daily-brief generation and chat tool calls."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BodyMetric,
    Goal,
    HeartRateSample,
    HRVReading,
    ReadinessScore,
    SleepSession,
    User,
    Workout,
)


def gather_recent(db: Session, user: User, days: int = 14) -> dict[str, Any]:
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
    }


def _workout(w: Workout) -> dict:
    return {
        "id": str(w.id),
        "source": w.source,
        "sport": w.sport,
        "started_at": w.started_at.isoformat(),
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
        "date": s.started_at.date().isoformat(),
        "total_min": round(total / 60, 1) if total else None,
        "deep_min": round(s.deep_s / 60, 1) if s.deep_s else None,
        "rem_min": round(s.rem_s / 60, 1) if s.rem_s else None,
        "efficiency": s.efficiency,
    }


def _hrv(h: HRVReading) -> dict:
    return {
        "source": h.source,
        "measured_at": h.measured_at.isoformat(),
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
        "measured_at": b.measured_at.isoformat(),
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
    return [{"measured_at": r.measured_at.isoformat(), "bpm": r.bpm} for r in rows]


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
