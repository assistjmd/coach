"""One allowance across every surface that spends model tokens.

Chat and session adjustment both call Anthropic on the athlete's behalf, so both
have to be metered — leaving the adjust endpoint unmetered, on the *expensive*
model, would make it the cheapest way for one user to run up the bill.

They draw on separate allowances rather than one shared pool. Sharing meant
someone who spent the day asking questions could no longer change their workout,
and of the two, changing the workout is what the product is for. A handful of
adjustments a day is plenty; needing a seventh means arguing with the coach
rather than training.

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


def limit_for(kind: str) -> int:
    cfg = settings()
    return cfg.adjust_daily_limit if kind == "adjust" else cfg.chat_daily_message_limit


def used_recently(db: Session, user: User, kind: str = "chat") -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return (
        db.query(func.count(ChatMessage.id))
        .filter(
            ChatMessage.user_id == user.id,
            ChatMessage.role == "user",
            ChatMessage.kind == kind,
            ChatMessage.created_at >= since,
        )
        .scalar()
        or 0
    )


_EXHAUSTED = {
    "chat": "That's all {limit} questions for today. More in a few hours.",
    "adjust": (
        "You've changed today's session {limit} times already. "
        "Pick one and go and do it."
    ),
}


def enforce(db: Session, user: User, kind: str = "chat") -> None:
    """Raise if the athlete has spent that surface's allowance."""
    limit = limit_for(kind)
    if used_recently(db, user, kind) >= limit:
        # 429 rather than 403: it is a rate limit, and the client should say when
        # it resets rather than presenting it as a permissions problem.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            _EXHAUSTED[kind].format(limit=limit),
        )
