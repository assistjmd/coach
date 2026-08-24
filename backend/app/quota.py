"""One allowance across every surface that spends model tokens.

Chat and session adjustment both call Anthropic on the athlete's behalf, so both
have to draw on the same budget. Metering only chat would leave the adjust
endpoint — which runs on the *expensive* model — as the cheapest way for one
user to run up the bill.

A rolling 24-hour window rather than a calendar day: a calendar reset lets
someone spend a whole allowance at 23:59 and another at 00:01, which is exactly
the burst this exists to prevent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChatMessage, User


def used_recently(db: Session, user: User) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return (
        db.query(func.count(ChatMessage.id))
        .filter(
            ChatMessage.user_id == user.id,
            ChatMessage.role == "user",
            ChatMessage.created_at >= since,
        )
        .scalar()
        or 0
    )


def enforce(db: Session, user: User) -> None:
    """Raise if the athlete has spent their allowance."""
    limit = settings().chat_daily_message_limit
    if used_recently(db, user) >= limit:
        # 429 rather than 403: it is a rate limit, and the client should say when
        # it resets rather than presenting it as a permissions problem.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"That's all {limit} questions for today. More in a few hours.",
        )
