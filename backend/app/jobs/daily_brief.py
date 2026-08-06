"""Daily-brief generation: one row per user per day, in their own morning."""

from __future__ import annotations

import argparse
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai.brief import generate_daily_brief
from app.db import SessionLocal
from app.models import DailyBrief, User

log = logging.getLogger("coach.brief")

# Local hour at which someone's brief should be waiting for them. Early enough
# to read before training, late enough that the night's sleep has synced.
TARGET_LOCAL_HOUR = 6


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
                if not _is_target_hour(u, now):
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
                generate_daily_brief(db, u, day=local_day)
                log.info("brief generated for user %s (%s)", u.id, local_day)
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
    return now.astimezone(_zone(user)).hour == TARGET_LOCAL_HOUR


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
