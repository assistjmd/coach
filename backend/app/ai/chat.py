"""Chat endpoint backing — streams Claude responses with tool use over user data.

For MVP, we pre-fetch the user's last 14 days of data and pass it as a context
block (simpler than tool-use round-trips, still benefits from prompt caching).
Tool-use upgrade is a follow-on once chat usage patterns are clearer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import anthropic
from sqlalchemy.orm import Session

from app.ai.data import gather_recent, user_profile
from app.ai.prompts import SYSTEM_PROMPT, user_context_block
from app.config import settings
from app.models import ChatMessage, User


def stream_chat(db: Session, user: User, message: str) -> Iterator[str]:
    cfg = settings()
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    snap = gather_recent(db, user, days=14)
    profile = user_profile(user, snap.get("goal"))

    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(20)
        .all()
    )
    history.reverse()
    msgs = [{"role": m.role, "content": m.content} for m in history if m.content]
    # last user message is already in history (we wrote it before calling). If not present, append.
    if not msgs or msgs[-1]["role"] != "user":
        msgs.append({"role": "user", "content": message})

    with client.messages.stream(
        # Chat runs on the cheap model deliberately. It is the only unbounded
        # call in the app, and the answers are short; the daily brief keeps the
        # stronger model because it runs once and is the output that matters.
        model=cfg.anthropic_model_cheap,
        max_tokens=cfg.chat_max_output_tokens,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {
                "type": "text",
                "text": user_context_block(profile),
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": f"USER DATA (last 14 days):\n{json.dumps(snap, default=str)}",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=msgs,
    ) as stream:
        for text in stream.text_stream:
            yield text
