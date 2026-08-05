"""Insights: the day-by-day record of what was asked and what happened."""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analytics import compliance as comp
from app.analytics.load import daily_loads
from app.auth.deps import current_user
from app.db import get_db
from app.models import DailyBrief, User, Workout

router = APIRouter(prefix="/v1/insights", tags=["insights"])

# Bounded so a client cannot ask for five years and pull every workout the
# athlete has ever logged into one response.
_MAX_RANGE_DAYS = 400


class WorkoutSummary(BaseModel):
    sport: str | None
    duration_s: int | None
    distance_m: float | None
    avg_hr: int | None
    source: str


class DayEntry(BaseModel):
    day: date
    load: float
    # "trimp" when every session that day had heart rate, "duration" when any
    # was estimated. The client renders estimates differently.
    load_method: str
    readiness: int | None
    prescription: str | None
    session_planned: bool | None
    compliance: str
    # True when the athlete corrected the inference, so the UI can say so rather
    # than presenting their own edit back to them as a judgement.
    corrected: bool
    workouts: list[WorkoutSummary]


class CalendarResponse(BaseModel):
    start: date
    end: date
    days: list[DayEntry]
    # Week-over-week load change, as a percentage. Null when there isn't enough
    # history to compare against — better than showing a swing off one week.
    load_trend_pct: float | None


class ComplianceUpdate(BaseModel):
    compliance: Literal["complete", "partial", "missed", "rest"]


@router.get("/calendar", response_model=CalendarResponse)
def calendar(
    start: date = Query(...),
    end: date = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CalendarResponse:
    if end < start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end must not precede start")
    if (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"range too wide — {_MAX_RANGE_DAYS} days maximum",
        )

    loads = daily_loads(db, user.id, start, end)
    briefs = {
        b.day: b
        for b in db.query(DailyBrief).filter(
            DailyBrief.user_id == user.id,
            DailyBrief.day >= start,
            DailyBrief.day <= end,
        )
    }

    workouts_by_day: dict[date, list[Workout]] = {}
    for w in (
        db.query(Workout)
        .filter(
            Workout.user_id == user.id,
            func.date(Workout.started_at) >= start,
            func.date(Workout.started_at) <= end,
        )
        .order_by(Workout.started_at)
    ):
        workouts_by_day.setdefault(w.started_at.date(), []).append(w)

    # The athlete's own median training day, used to tell a full session from a
    # token one. Median over non-zero days: including rest days would drag the
    # baseline toward zero and make almost anything count as complete.
    trained = [v for v, _ in loads.values() if v > 0]
    typical = statistics.median(trained) if trained else 0.0

    days: list[DayEntry] = []
    cursor = start
    while cursor <= end:
        load, method = loads.get(cursor, (0.0, "trimp"))
        brief = briefs.get(cursor)

        # Recompute inference on read rather than trusting a stored value: a
        # workout can arrive days late from a provider backfill, and a day that
        # was a miss at write time may not be one now.
        auto = comp.infer(brief, load, typical)
        if brief is not None and brief.compliance_auto != auto:
            brief.compliance_auto = auto

        days.append(
            DayEntry(
                day=cursor,
                load=load,
                load_method=method,
                readiness=brief.readiness_score if brief else None,
                prescription=brief.training_prescription if brief else None,
                session_planned=brief.session_planned if brief else None,
                compliance=comp.resolve(brief) if brief else auto,
                corrected=bool(brief and brief.compliance_override),
                workouts=[
                    WorkoutSummary(
                        sport=w.sport,
                        duration_s=w.duration_s,
                        distance_m=w.distance_m,
                        avg_hr=w.avg_hr,
                        source=w.source,
                    )
                    for w in workouts_by_day.get(cursor, [])
                ],
            )
        )
        cursor += timedelta(days=1)
    db.commit()

    return CalendarResponse(
        start=start,
        end=end,
        days=days,
        load_trend_pct=_week_over_week(loads, end),
    )


def _week_over_week(
    loads: dict[date, tuple[float, str]], end: date
) -> float | None:
    """Change between the last seven days and the seven before them."""
    recent = sum(loads.get(end - timedelta(days=i), (0.0, ""))[0] for i in range(7))
    prior = sum(loads.get(end - timedelta(days=i), (0.0, ""))[0] for i in range(7, 14))
    if prior <= 0:
        # No baseline. Reporting "+100%" off a zero week is noise, not a trend.
        return None
    return round((recent - prior) / prior * 100, 1)


@router.patch("/day/{day}", response_model=DayEntry)
def correct_compliance(
    day: date,
    body: ComplianceUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DayEntry:
    """Override the inferred compliance for one day.

    Only days with a brief can be corrected: without a prescription there is
    nothing to have complied with.
    """
    brief = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id, DailyBrief.day == day)
        .one_or_none()
    )
    if brief is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no brief for {day}")

    brief.compliance_override = body.compliance
    db.commit()

    loads = daily_loads(db, user.id, day, day)
    load, method = loads.get(day, (0.0, "trimp"))
    workouts = (
        db.query(Workout)
        .filter(
            Workout.user_id == user.id,
            func.date(Workout.started_at) == day,
        )
        .order_by(Workout.started_at)
        .all()
    )
    return DayEntry(
        day=day,
        load=load,
        load_method=method,
        readiness=brief.readiness_score,
        prescription=brief.training_prescription,
        session_planned=brief.session_planned,
        compliance=comp.resolve(brief),
        corrected=True,
        workouts=[
            WorkoutSummary(
                sport=w.sport,
                duration_s=w.duration_s,
                distance_m=w.distance_m,
                avg_hr=w.avg_hr,
                source=w.source,
            )
            for w in workouts
        ],
    )
