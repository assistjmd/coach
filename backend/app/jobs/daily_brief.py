"""Daily-brief generation: one row per user per day, in their own morning."""

from __future__ import annotations

import argparse
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai.brief import generate_daily_brief
from app.analytics.overnight import last_night_arrived
from app.db import SessionLocal
from app.models import DailyBrief, User

log = logging.getLogger("coach.brief")

# Local hour from which a brief may be written. The earliest anyone wants it.
TARGET_LOCAL_HOUR = 6

# And the hour by which one is written regardless.
#
# Between these two the job waits for last night's sleep to arrive rather than
# guessing. It frequently has not synced at 6am — Apple Health receives it when
# the watch syncs, and we receive it when the app next opens — and a brief built
# without it silently uses the night *before* last, which is worse than no
# readiness at all because it looks correct.
#
# The deadline exists because plenty of people do not track sleep, and waiting
# forever would mean they never get a brief.
DEADLINE_LOCAL_HOUR = 11


def run_for_all_users(now: datetime | None = None) -> None:
    """Generate briefs for every user whose local time is now the target hour.

    Called hourly rather than once a day. The job previously ran at a fixed
    10:00 UTC for everyone, which is 3am in California and 8pm in Sydney — so a
    "morning" brief arrived in the middle of the night for most of the world, and
    was built from a day of data that hadn't happened yet.
    """
    now = now or datetime.now(tz=ZoneInfo("UTC"))
    with SessionLocal() as db:
        users = db.query(User).all()
        for u in users:
            try:
                local_hour = now.astimezone(_zone(u)).hour
                if not TARGET_LOCAL_HOUR <= local_hour <= DEADLINE_LOCAL_HOUR:
                    continue
                local_day = now.astimezone(_zone(u)).date()
                # Skip anyone who already has today's brief. The hourly cadence
                # means a DST shift or a restart can revisit the same local hour,
                # and regenerating would burn tokens to overwrite an identical row.
                exists = (
                    db.query(DailyBrief.id)
                    .filter(DailyBrief.user_id == u.id, DailyBrief.day == local_day)
                    .first()
                )
                if exists:
                    continue

                slept = last_night_arrived(db, u, now)
                if not slept and local_hour < DEADLINE_LOCAL_HOUR:
                    # Try again next hour. Nothing is written, so a later run
                    # produces the brief with the night included.
                    log.info(
                        "waiting for last night's sleep, user %s (%s, %02d:00)",
                        u.id, local_day, local_hour,
                    )
                    continue

                brief = generate_daily_brief(db, u, day=local_day)
                brief.had_overnight_sleep = slept
                db.commit()
                log.info(
                    "brief generated for user %s (%s, overnight_sleep=%s)",
                    u.id, local_day, slept,
                )
            except Exception:
                log.exception("daily brief failed for user %s", u.id)


def _zone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        # A stale or malformed identifier must not stop this user getting a
        # brief, and must not take the whole run down with it.
        log.warning("unknown timezone %r for user %s", user.timezone, user.id)
        return ZoneInfo("UTC")


def _is_target_hour(user: User, now: datetime) -> bool:
    """Kept for the tests that predate the waiting window."""
    return now.astimezone(_zone(user)).hour == TARGET_LOCAL_HOUR


def regenerate_if_sleep_landed(db, user: User) -> bool:
    """Rewrite today's brief once, if it was written without last night's sleep.

    Called after a sleep ingest. Someone who opens the app at 8am, having been
    given a deadline-free brief at 6am that waited and gave up, should not spend
    the day looking at readiness computed from the wrong night.

    Bounded by the flag itself: once a brief has the night, it is never
    regenerated again, so a chatty sync cannot spend tokens in a loop.
    """
    from app.dates import today_for

    day = today_for(user)
    brief = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id, DailyBrief.day == day)
        .one_or_none()
    )
    if brief is None or brief.had_overnight_sleep:
        return False
    if not last_night_arrived(db, user):
        return False

    generate_daily_brief(db, user, day=day)
    brief.had_overnight_sleep = True
    db.commit()
    log.info("brief regenerated with last night's sleep for user %s (%s)", user.id, day)
    return True


def run_for_user(user_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise SystemExit(f"user {user_id} not found")
        generate_daily_brief(db, u)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="UUID of a single user; otherwise run for all")
    args = parser.parse_args()
    if args.user:
        run_for_user(uuid.UUID(args.user))
    else:
        run_for_all_users()
