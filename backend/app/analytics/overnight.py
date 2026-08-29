"""Has last night's sleep actually arrived?

The daily brief was generated at 6am local on the assumption that the night had
synced by then. It frequently has not: Apple Health receives sleep when the
watch syncs, and we receive it when the app next opens or background delivery
fires. Neither is guaranteed before dawn.

The result was a brief built on the night *before* last — readiness for Friday
computed from Wednesday night's sleep — which is worse than no readiness at all,
because it looks correct.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.dates import local_day, zone_for
from app.models import SleepSession, User

# A nap is not a night. Anything shorter than this ending today is somebody who
# went back to bed after breakfast, and waiting for it would be waiting for
# something that already came.
_MIN_NIGHT = timedelta(hours=2)


def last_night_arrived(db: Session, user: User, now: datetime | None = None) -> bool:
    """Whether we hold a sleep session the athlete woke from today.

    Keyed on when the sleep *ended*, in their zone: you wake up today, having
    fallen asleep yesterday. Keying on the start would miss every night that
    began before midnight, which is most of them.
    """
    now = now or datetime.now(timezone.utc)
    tz = zone_for(user)
    today = local_day(now, tz)

    rows = (
        db.query(SleepSession)
        .filter(
            SleepSession.user_id == user.id,
            SleepSession.ended_at >= now - timedelta(hours=36),
        )
        .all()
    )
    for row in rows:
        ended = row.ended_at
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        if local_day(ended, tz) != today:
            continue
        started = row.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if ended - started >= _MIN_NIGHT:
            return True
    return False
