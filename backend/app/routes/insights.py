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
from app.dates import local_day, zone_for
from app.db import get_db
from app.models import DailyBrief, DayLog, User, Workout

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


class SessionItem(BaseModel):
    id: str
    text: str
    # Prescribed counts, absent when the item isn't countable (an easy 20 minutes
    # has neither). Their presence is what tells the client to offer a stepper.
    sets: int | None = None
    reps: int | None = None


class ItemActual(BaseModel):
    """What was actually done, when it differed from the prescription."""
    sets: int | None = None
    reps: int | None = None


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
    # The prescription split into tickable pieces, plus which the athlete ticked.
    session_items: list[SessionItem]
    completed_items: list[str]
    # Keyed by item id. Only present for items done differently from prescribed.
    item_actuals: dict[str, ItemActual]
    journal: str | None


class CalendarResponse(BaseModel):
    start: date
    end: date
    days: list[DayEntry]
    # Week-over-week load change, as a percentage. Null when there isn't enough
    # history to compare against — better than showing a swing off one week.
    load_trend_pct: float | None


class ComplianceUpdate(BaseModel):
    compliance: Literal["complete", "partial", "missed", "rest"]


class DayLogUpdate(BaseModel):
    journal: str | None = None
    completed_items: list[str] | None = None
    item_actuals: dict[str, ItemActual] | None = None


def _entry(
    day: date,
    brief: DailyBrief | None,
    log: DayLog | None,
    load: float,
    method: str,
    workouts: list[Workout],
    fallback_status: str,
) -> DayEntry:
    """Assemble one day. Single builder so the three endpoints that return a
    DayEntry cannot drift apart in what they include."""
    completed = list(log.completed_items or []) if log else []
    return DayEntry(
        day=day,
        load=load,
        load_method=method,
        readiness=brief.readiness_score if brief else None,
        prescription=brief.training_prescription if brief else None,
        session_planned=brief.session_planned if brief else None,
        compliance=comp.resolve(brief, completed) if brief else fallback_status,
        corrected=bool(brief and brief.compliance_override),
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
        session_items=[
            SessionItem(
                id=i["id"], text=i["text"], sets=i.get("sets"), reps=i.get("reps")
            )
            for i in (brief.session_items or [])
            if isinstance(i, dict) and i.get("id") and i.get("text")
        ] if brief else [],
        completed_items=completed,
        item_actuals={
            k: ItemActual(**v)
            for k, v in ((log.item_actuals or {}) if log else {}).items()
            if isinstance(v, dict)
        },
        journal=log.journal if log else None,
    )


def _day_context(db: Session, user: User, day: date):
    """Everything one day needs, for the single-day endpoints."""
    brief = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id, DailyBrief.day == day)
        .one_or_none()
    )
    log = (
        db.query(DayLog)
        .filter(DayLog.user_id == user.id, DayLog.day == day)
        .one_or_none()
    )
    load, method = daily_loads(db, user.id, day, day).get(day, (0.0, "trimp"))
    workouts = (
        db.query(Workout)
        .filter(
            Workout.user_id == user.id,
            func.date(Workout.started_at) >= day - timedelta(days=1),
            func.date(Workout.started_at) <= day + timedelta(days=1),
        )
        .order_by(Workout.started_at)
        .all()
    )
    tz = zone_for(user)
    workouts = [w for w in workouts if local_day(w.started_at, tz) == day]
    return brief, log, load, method, workouts


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

    logs = {
        entry.day: entry
        for entry in db.query(DayLog).filter(
            DayLog.user_id == user.id, DayLog.day >= start, DayLog.day <= end
        )
    }

    # Grouped by the athlete's local day, and widened by one day on each side
    # so a session that is inside the local window but outside it in UTC is not
    # dropped by the SQL comparison.
    tz = zone_for(user)
    workouts_by_day: dict[date, list[Workout]] = {}
    for w in (
        db.query(Workout)
        .filter(
            Workout.user_id == user.id,
            func.date(Workout.started_at) >= start - timedelta(days=1),
            func.date(Workout.started_at) <= end + timedelta(days=1),
        )
        .order_by(Workout.started_at)
    ):
        day = local_day(w.started_at, tz)
        if start <= day <= end:
            workouts_by_day.setdefault(day, []).append(w)

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
            _entry(
                day=cursor,
                brief=brief,
                log=logs.get(cursor),
                load=load,
                method=method,
                workouts=workouts_by_day.get(cursor, []),
                fallback_status=auto,
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

    _, log, load, method, workouts = _day_context(db, user, day)
    return _entry(day, brief, log, load, method, workouts, "unknown")


@router.put("/day/{day}/log", response_model=DayEntry)
def update_log(
    day: date,
    body: DayLogUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DayEntry:
    """Record what actually happened: the athlete's note, and which prescribed
    items they ticked off.

    Works on any day, including ones with no brief — a session done before they
    installed the app still deserves a journal entry. Fields are updated only
    when present, so saving a note cannot silently clear the checkboxes.
    """
    brief, log, load, method, workouts = _day_context(db, user, day)
    if log is None:
        log = DayLog(user_id=user.id, day=day)
        db.add(log)

    if body.journal is not None:
        # Empty string means "cleared", which is distinct from "not sent".
        log.journal = body.journal.strip() or None
    if body.completed_items is not None:
        valid = {
            i["id"] for i in (brief.session_items or [])
            if isinstance(i, dict) and i.get("id")
        } if brief else set()
        # Drop ids that no longer exist. A regenerated brief can rename or remove
        # an item, and keeping a stale id would inflate the completion count
        # against a checkbox nobody can see.
        log.completed_items = [i for i in body.completed_items if i in valid]
    if body.item_actuals is not None:
        valid_ids = {
            i["id"] for i in (brief.session_items or [])
            if isinstance(i, dict) and i.get("id")
        } if brief else set()
        # Same reasoning as the ticks: an entry for an item the brief no longer
        # contains would be unreachable in the UI and misleading in the record.
        # Entries with nothing set are dropped rather than stored as empty
        # objects, so "logged nothing" doesn't masquerade as "logged a change".
        log.item_actuals = {
            k: v.model_dump(exclude_none=True)
            for k, v in body.item_actuals.items()
            if k in valid_ids and v.model_dump(exclude_none=True)
        } or None
    db.commit()
    db.refresh(log)

    return _entry(day, brief, log, load, method, workouts, "unknown")
