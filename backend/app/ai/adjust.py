"""Letting the athlete talk Will into changing today's session.

The prescription used to be take-it-or-leave-it. Someone with no barbell, or
thirty minutes instead of an hour, could only ignore the session or do it wrong
— and both corrupt the compliance signal that feeds straight back into tomorrow's
brief. Negotiating the session and recording the result keeps the data honest
about what actually happened.

Runs on the brief model rather than the cheap chat model. This has to emit valid
structured JSON that overwrites a stored session, and it is low-frequency. Chat's
model is chosen for cost on an unbounded surface; this is a bounded one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic
from sqlalchemy.orm import Session

from app.ai.brief import clean_items
from app.ai.validation import AdjustPayload
from app.ai.data import gather_recent, user_profile
from app.ai.prompts import ADJUST_PROMPT, SYSTEM_PROMPT, user_context_block
from app.config import settings
from app.dates import today_for
from app.models import DailyBrief, DayLog, User

log = logging.getLogger(__name__)


class NoSessionToday(RuntimeError):
    """No brief exists for today, so there is nothing to adjust."""


class UnreadableReply(RuntimeError):
    """The model answered with something that is not the JSON we asked for.

    It happens: a request that lands near a guardrail sometimes gets a plain
    prose refusal instead of a structured one. That is a bad reply, not a
    server fault, and the athlete should be told to rephrase rather than shown
    a 500.
    """


@dataclass
class AdjustResult:
    reply: str
    changed: bool
    brief: DailyBrief


def adjust_session(db: Session, user: User, message: str) -> AdjustResult:
    cfg = settings()
    day = today_for(user)
    brief = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id, DailyBrief.day == day)
        .one_or_none()
    )
    if brief is None:
        raise NoSessionToday("no brief for today")

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key, timeout=90, max_retries=0)
    snap = gather_recent(db, user, days=14)
    profile = user_profile(user, snap.get("goal"))

    current = {
        "training_prescription": brief.training_prescription,
        "readiness_summary": brief.readiness_summary,
        "readiness_score": brief.readiness_score,
        # The guardrail hinges on this: a false value means today was rest, and
        # no amount of asking turns it into a session.
        "session_planned": brief.session_planned,
        "session_items": brief.session_items or [],
    }

    resp = client.messages.create(
        model=cfg.anthropic_model_default,
        max_tokens=1200,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": ADJUST_PROMPT, "cache_control": {"type": "ephemeral"}},
            {
                "type": "text",
                "text": user_context_block(profile),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {day.isoformat()}.\n\n"
                    f"WHAT YOU PRESCRIBED TODAY:\n{json.dumps(current, default=str)}\n\n"
                    f"THE ATHLETE SAYS:\n{message}\n\n"
                    "Return ONLY a JSON object, no prose around it, with:\n"
                    "  reply - what you say back, in your voice. Two or three "
                    "sentences at most. If you are changing the session, say what "
                    "changed. If you are declining, say why in one line and offer "
                    "what you will do instead.\n"
                    "  changed - true only if you are actually revising the "
                    "session. False when you are answering a question or "
                    "declining.\n"
                    "  training_prescription - the updated one-line call. When "
                    "changed is false, repeat the existing one unaltered.\n"
                    "  session_items - the full revised list, same shape as "
                    "above: 'id', 'text', and integer 'sets'/'reps' where the "
                    "item is countable. Return the existing list unchanged when "
                    "changed is false. Reuse an item's existing id when it "
                    "survives the change, so anything already ticked off stays "
                    "ticked.\n"
                    f"DATA:\n{json.dumps(snap, default=str)}"
                ),
            }
        ],
    )

    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
        parsed = AdjustPayload.model_validate(parsed).model_dump()
    except ValueError as e:
        # Logged in full: the only way to see which requests push the model off
        # JSON is to keep the reply that did it.
        log.error("adjust: unusable model reply for user %s", user.id)
        raise UnreadableReply("could not read the reply") from e
    if not isinstance(parsed, dict):
        log.error("adjust: reply was not an object for user %s", user.id)
        raise UnreadableReply("reply was not an object")

    reply = str(parsed.get("reply") or "").strip()
    changed = bool(parsed.get("changed"))

    if changed:
        items = clean_items(parsed.get("session_items"))
        if items:
            brief.session_items = items
            prescription = str(parsed.get("training_prescription") or "").strip()
            if prescription:
                brief.training_prescription = prescription
            brief.adjusted_at = datetime.now(timezone.utc)
            # Their words, not a summary. Fed back into future briefs, where the
            # request itself is the useful signal.
            brief.adjustment_note = message.strip()[:500]
            _drop_orphaned_ticks(db, user, brief)
        else:
            # The model claimed a change and produced nothing usable. Keeping the
            # original session is safer than storing an empty one.
            log.warning("adjust returned no valid items for user %s", user.id)
            changed = False

    db.commit()
    db.refresh(brief)
    return AdjustResult(reply=reply, changed=changed, brief=brief)


def _drop_orphaned_ticks(db: Session, user: User, brief: DailyBrief) -> None:
    """Remove ticks against exercises the revised session no longer contains.

    A tick surviving a swap would count toward completion for something the
    athlete can no longer see, which inflates compliance against a session that
    was never prescribed.
    """
    log_entry = (
        db.query(DayLog)
        .filter(DayLog.user_id == user.id, DayLog.day == brief.day)
        .one_or_none()
    )
    if log_entry is None:
        return
    live = {
        i["id"] for i in (brief.session_items or [])
        if isinstance(i, dict) and i.get("id")
    }
    if log_entry.completed_items:
        log_entry.completed_items = [i for i in log_entry.completed_items if i in live]
    if log_entry.item_actuals:
        log_entry.item_actuals = {
            k: v for k, v in log_entry.item_actuals.items() if k in live
        } or None
