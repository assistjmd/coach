"""Nightly daily-brief generation: one row per user per day."""

from __future__ import annotations

import argparse
import logging
import uuid

from app.ai.brief import generate_daily_brief
from app.db import SessionLocal
from app.models import User

log = logging.getLogger("coach.brief")


def run_for_all_users() -> None:
    with SessionLocal() as db:
        users = db.query(User).all()
        for u in users:
            try:
                generate_daily_brief(db, u)
            except Exception:
                log.exception("daily brief failed for user %s", u.id)


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
