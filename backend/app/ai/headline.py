"""Update what the coach says about today, without touching what it asked for.

The brief is written in the morning and contradicted by the day: it still says
"get your run in" at 8pm to someone who ran at lunchtime. Refreshing that was
implemented by regenerating the whole brief, which also replaced `session_items`
— so logging a workout swapped a shoulder-and-back day for a leg day underneath
someone who was part-way through it, and the exercises they had already ticked
off vanished.

The plan is not the headline. Once the coach has asked for something and the
athlete has started it, the only thing allowed to move is what the coach *says*
about the day. Changing the prescription mid-session is a different act
entirely, and there is already a place for it: the athlete asking.
"""

from __future__ import annotations

import json
import logging

import anthropic
from sqlalchemy.orm import Session

from app.ai.data import gather_recent, user_profile
from app.ai.prompts import SYSTEM_PROMPT, user_context_block
from app.config import settings
from app.models import DailyBrief, User

log = logging.getLogger(__name__)


class HeadlineUnavailable(RuntimeError):
    """The model did not return something usable. The old headline stands."""


def refresh_headline(db: Session, user: User, brief: DailyBrief) -> bool:
    """Rewrite the readiness read for a brief whose day has moved on.

    Returns whether anything changed. The session is passed in as context so the
    new wording can refer to it, and is never written back.
    """
    cfg = settings()
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    snap = gather_recent(db, user, days=14)
    profile = user_profile(user, snap.get("goal"))

    current = {
        "readiness_summary": brief.readiness_summary,
        "training_prescription": brief.training_prescription,
        "session_items": brief.session_items or [],
        "readiness_score": brief.readiness_score,
    }

    resp = client.messages.create(
        # The cheap model: this is a rewrite of two sentences against context the
        # expensive one already reasoned over this morning.
        model=cfg.anthropic_model_cheap,
        max_tokens=400,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {
                "type": "text",
                "text": user_context_block(profile),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{
            "role": "user",
            "content": (
                "Something has happened since you wrote this morning's brief — a "
                "session was logged, or items were ticked off.\n\n"
                f"THIS MORNING YOU WROTE:\n{json.dumps(current, default=str)}\n\n"
                "Rewrite ONLY the opening read so it matches where the day "
                "actually stands. If they have done the session, say so. If they "
                "have done something else, account for it. Do not restate the "
                "session and do not prescribe anything new — the plan is already "
                "set and they may be part-way through it.\n\n"
                "Return ONLY a JSON object with:\n"
                "  readiness_summary - one or two sentences, your normal voice\n"
                "  readiness_score - integer 0-100, or null to keep the current one\n"
                f"DATA:\n{json.dumps(snap, default=str)}"
            ),
        }],
    )

    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("not an object")
    except ValueError as e:
        log.error("headline refresh unparseable for user %s — %r", user.id, raw[:300])
        raise HeadlineUnavailable("no usable headline") from e

    summary = str(parsed.get("readiness_summary") or "").strip()
    if not summary:
        return False

    brief.readiness_summary = summary
    score = parsed.get("readiness_score")
    if isinstance(score, (int, float)):
        brief.readiness_score = min(max(int(score), 0), 100)
    return True
