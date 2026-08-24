import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.brief import generate_daily_brief
from app.auth.deps import current_user
from app.dates import today_for
from app.db import get_db
from app.models import DailyBrief, DayLog, HRVReading, SleepSession, User, Workout

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])

# How stale a brief may be before we'd rather generate a fresh one.
_MAX_BRIEF_AGE = timedelta(days=1)


class SessionItem(BaseModel):
    id: str
    text: str
    sets: int | None = None
    reps: int | None = None


class ItemActual(BaseModel):
    sets: int | None = None
    reps: int | None = None


class TodayBrief(BaseModel):
    day: date
    readiness_summary: str
    training_prescription: str
    rationale: str | None
    flags: dict | None
    # 0-100, and the number the home screen now leads with. It was computed and
    # stored from the first brief onward but never sent to the client, so the
    # app had prose about readiness and no way to show the score behind it.
    readiness_score: int | None = None
    stale: bool = False
    # Today's session as tickable pieces, and which are already done. Carried
    # here as well as on the calendar because ticking off a session as you do it
    # belongs on the screen you already have open, not two taps into history.
    session_items: list[SessionItem] = []
    completed_items: list[str] = []
    item_actuals: dict[str, ItemActual] = {}


@router.get("/today", response_model=TodayBrief)
def today(user: User = Depends(current_user), db: Session = Depends(get_db)) -> TodayBrief:
    # The user's today, not the server's. On Fly the server is UTC, which
    # rolls over at 7pm in Boston — the dashboard would start asking for a
    # brief that does not exist yet and fall back to yesterday's.
    today_ = today_for(user)
    brief = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id)
        .order_by(DailyBrief.day.desc())
        .first()
    )
    if brief is not None and brief.day == today_:
        completed, actuals = _log_state(db, user, brief.day)
        return _as_response(brief, completed=completed, actuals=actuals)

    # Nothing for today. The nightly job runs at a fixed hour, so a user who
    # signs up after it has already run would otherwise stare at "no brief yet"
    # until tomorrow — which is the first thing they'd see after onboarding.
    if _has_data(db, user):
        try:
            fresh = generate_daily_brief(db, user, day=today_)
            completed, actuals = _log_state(db, user, fresh.day)
            return _as_response(fresh, completed=completed, actuals=actuals)
        except Exception:
            log.exception("on-demand brief generation failed for user %s", user.id)

    if brief is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no brief yet — connect a tracker to get your first readiness brief",
        )
    # Fall back to the most recent brief, flagged so the client can say when it
    # is from rather than presenting yesterday's numbers as today's.
    completed, actuals = _log_state(db, user, brief.day)
    return _as_response(
        brief,
        stale=today_ - brief.day > _MAX_BRIEF_AGE,
        completed=completed,
        actuals=actuals,
    )


def _has_data(db: Session, user: User) -> bool:
    """Whether there is anything worth writing a brief about."""
    return any(
        db.query(model.id).filter(model.user_id == user.id).first() is not None
        for model in (SleepSession, Workout, HRVReading)
    )


def _as_response(
    brief: DailyBrief,
    stale: bool = False,
    completed: list[str] | None = None,
    actuals: dict[str, ItemActual] | None = None,
) -> TodayBrief:
    return TodayBrief(
        day=brief.day,
        readiness_summary=brief.readiness_summary,
        training_prescription=brief.training_prescription,
        rationale=brief.rationale,
        flags=brief.flags,
        readiness_score=brief.readiness_score,
        stale=stale,
        session_items=[
            SessionItem(
                id=i["id"], text=i["text"], sets=i.get("sets"), reps=i.get("reps")
            )
            for i in (brief.session_items or [])
            if isinstance(i, dict) and i.get("id") and i.get("text")
        ],
        completed_items=completed or [],
        item_actuals=actuals or {},
    )


def _log_state(
    db: Session, user: User, day: date
) -> tuple[list[str], dict[str, ItemActual]]:
    log_row = (
        db.query(DayLog)
        .filter(DayLog.user_id == user.id, DayLog.day == day)
        .one_or_none()
    )
    if log_row is None:
        return [], {}
    actuals = {
        k: ItemActual(**v)
        for k, v in (log_row.item_actuals or {}).items()
        if isinstance(v, dict)
    }
    return list(log_row.completed_items or []), actuals
