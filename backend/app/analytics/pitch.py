"""What to show someone being asked to pay.

The strongest case for the subscription is not a feature list — they accepted
that fourteen days ago — it is what actually happened to them since. Sessions
they completed, workouts they logged, whether their readiness moved.

Every figure here degrades. Someone who connected nothing has no case to make
from their own data, and inventing one would be both dishonest and obvious. When
there is nothing to say, this returns nothing and the paywall falls back to
plain copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analytics.consistency import consistency
from app.dates import today_for
from app.models import DailyBrief, User, Workout

# Below this many scored days a completion figure is noise, matching the rule
# the consistency card already uses.
_MIN_ASSESSED = 3
# A readiness trend needs two comparable windows either side.
_MIN_READINESS_DAYS = 8


@dataclass
class Pitch:
    # Short lines, most compelling first. Empty when nothing can honestly be said.
    lines: list[str] = field(default_factory=list)
    sessions_completed: int | None = None
    sessions_asked: int | None = None
    workouts_logged: int | None = None
    readiness_change: int | None = None
    days_with_coach: int = 0


def build(db: Session, user: User) -> Pitch:
    today = today_for(user)
    created = user.created_at.date() if user.created_at else today
    days = max((today - created).days, 0)

    p = Pitch(days_with_coach=days)

    c = consistency(db, user, days=max(days, 14))
    if c.assessed >= _MIN_ASSESSED:
        done = c.completed + c.partial
        p.sessions_completed = done
        p.sessions_asked = c.assessed
        p.lines.append(
            f"You've done {done} of the {c.assessed} sessions I asked for."
        )

    workouts = (
        db.query(func.count(Workout.id))
        .filter(Workout.user_id == user.id, Workout.started_at >= user.created_at)
        .scalar()
        or 0
    )
    if workouts:
        p.workouts_logged = workouts
        p.lines.append(
            f"{workouts} session{'s' if workouts != 1 else ''} logged and read."
        )

    delta = _readiness_change(db, user, today)
    if delta is not None:
        p.readiness_change = delta
        if delta >= 3:
            p.lines.append(f"Your readiness is up {delta} points since you started.")
        elif delta <= -3:
            # Said plainly rather than hidden. A number moving the wrong way is
            # the best argument for keeping the coach, and dropping it here
            # while showing it on the trend screen would be transparently
            # selective.
            p.lines.append(
                f"Your readiness is down {abs(delta)} since you started. "
                "Worth working out why."
            )
        else:
            p.lines.append("Your readiness has held steady.")

    if c.streak >= 3:
        p.lines.append(f"{c.streak} days running without missing a call.")

    return p


def _readiness_change(db: Session, user: User, today) -> int | None:
    """Recent readiness against where they began.

    Halves of the window rather than first-and-last day: two single readings
    could differ by more than any trend, and a number that swings on one bad
    night is not evidence of anything.
    """
    rows = (
        db.query(DailyBrief.day, DailyBrief.readiness_score)
        .filter(
            DailyBrief.user_id == user.id,
            DailyBrief.readiness_score.isnot(None),
            DailyBrief.day >= today - timedelta(days=60),
        )
        .order_by(DailyBrief.day)
        .all()
    )
    scores = [r[1] for r in rows]
    if len(scores) < _MIN_READINESS_DAYS:
        return None
    half = len(scores) // 2
    first = sum(scores[:half]) / half
    last = sum(scores[half:]) / (len(scores) - half)
    return round(last - first)
