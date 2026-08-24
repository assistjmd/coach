"""How closely the athlete follows what the coach asked for.

This is the number no wearable can show. Oura and Whoop report readiness because
they measure a body; neither has prescribed anything, so neither has a plan to
measure adherence against. Coach Will does, and `compliance.py` already resolves
each day into a status. Nothing surfaced it outside the Insights calendar.

The scoring decision that matters is what counts in the denominator. Rest days
are excluded entirely rather than scored as wins:

- Counted as wins, someone training twice a week sits near 100% for doing very
  little, and the number stops meaning anything.
- Counted as losses, someone is punished for correctly obeying an instruction to
  rest, which is the opposite of the behaviour we want.

So the score answers one question: of the days a session was asked for, how many
happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.analytics import compliance as comp
from app.dates import today_for
from app.models import DailyBrief, DayLog, User

# Below this many scored days the percentage is noise — one completed session
# reads as 100%, one missed as 0%. The client shows an empty state instead.
_MIN_ASSESSED = 3

_WEIGHT = {"complete": 1.0, "partial": 0.5, "missed": 0.0}


@dataclass(frozen=True)
class ConsistencyResult:
    # None until there are enough scored days to mean anything.
    score: int | None
    completed: int
    partial: int
    missed: int
    rest: int
    # Days that counted toward the score. The denominator, exposed so the client
    # can say "8 of 10" rather than only a percentage.
    assessed: int
    streak: int


def consistency(db: Session, user: User, days: int = 14) -> ConsistencyResult:
    today = today_for(user)
    start = today - timedelta(days=days)

    briefs = {
        b.day: b
        for b in db.query(DailyBrief).filter(
            DailyBrief.user_id == user.id,
            DailyBrief.day >= start,
            DailyBrief.day <= today,
        )
    }
    logs = {
        entry.day: entry
        for entry in db.query(DayLog).filter(
            DayLog.user_id == user.id, DayLog.day >= start, DayLog.day <= today
        )
    }

    def status_on(day):
        brief = briefs.get(day)
        if brief is None:
            return "unknown"
        log = logs.get(day)
        # The existing precedence chain: explicit override, then ticks, then
        # inference. Reimplementing it here would let the two drift.
        return comp.resolve(brief, log.completed_items if log else None)

    counts = {"complete": 0, "partial": 0, "missed": 0, "rest": 0}
    earned = 0.0
    assessed = 0

    # Today is excluded from the score. At the hour most people open the app the
    # session has not happened yet, and counting it would read as a miss.
    day = start
    while day < today:
        state = status_on(day)
        if state in counts:
            counts[state] += 1
        if state in _WEIGHT:
            earned += _WEIGHT[state]
            assessed += 1
        day += timedelta(days=1)

    score = round(earned / assessed * 100) if assessed >= _MIN_ASSESSED else None

    return ConsistencyResult(
        score=score,
        completed=counts["complete"],
        partial=counts["partial"],
        missed=counts["missed"],
        rest=counts["rest"],
        assessed=assessed,
        streak=_streak(status_on, today),
    )


def _streak(status_on, today) -> int:
    """Consecutive days back from yesterday without a miss.

    Starts at yesterday deliberately. Today's session is usually not done when
    someone opens the app in the morning, and a streak that reads zero every
    morning until you train is worse than showing none at all.

    A rest day continues the streak — following an instruction to rest is
    compliance, not a gap in it. An unknown day stops the walk without being a
    miss: there is no brief, so there is nothing to have followed.
    """
    streak = 0
    day = today - timedelta(days=1)
    # 365 is a backstop, not a limit anyone will reach in this beta.
    for _ in range(365):
        state = status_on(day)
        if state in ("complete", "rest"):
            streak += 1
        elif state == "partial":
            # Partial credit keeps the streak but is not itself a full day.
            streak += 1
        else:
            break
        day -= timedelta(days=1)
    return streak
