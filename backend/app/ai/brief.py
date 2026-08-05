"""Daily-brief generation. Run nightly per user."""

from __future__ import annotations

import json
from datetime import date

import anthropic
from sqlalchemy.orm import Session

from app.ai.data import gather_recent, user_profile
from app.ai.prompts import SYSTEM_PROMPT, user_context_block
from app.config import settings
from app.models import DailyBrief, User


def generate_daily_brief(db: Session, user: User, day: date | None = None) -> DailyBrief:
    cfg = settings()
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    snap = gather_recent(db, user, days=14)
    profile = user_profile(user, snap.get("goal"))
    target_day = day or date.today()

    resp = client.messages.create(
        model=cfg.anthropic_model_default,
        max_tokens=1500,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
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
                    f"Today is {target_day.isoformat()}. Using the data below, return ONLY a "
                    f"JSON object, no prose around it, with these keys:\n"
                    f"  readiness_summary  - what you noticed. One or two sentences. Lead with "
                    f"the thing that changed, not a greeting.\n"
                    f"  training_prescription - your call for today, specific enough to act on "
                    f"(sets, duration, intensity). State it in the first person.\n"
                    f"  rationale - one sentence on why. Not a paragraph.\n"
                    f"  readiness_score - integer 0-100 matching your read above. "
                    f"Under 40 is poor, 40-64 moderate, 65+ good. The app colours "
                    f"the day by this, so it must agree with what you wrote.\n"
                    f"  session_planned - true if you are asking for a training "
                    f"session today, false if you are prescribing rest. Be honest: "
                    f"this decides whether a day with no workout counts as a missed "
                    f"session or as following instructions.\n"
                    f"  flags - object with boolean keys such as 'illness_risk', 'overreaching'.\n"
                    f"Each string is prose in your normal voice. No headers, no bullets, no "
                    f"sign-off.\n\n"
                    f"DATA:\n{json.dumps(snap, default=str)}"
                ),
            }
        ],
    )
    raw = "".join(block.text for block in resp.content if block.type == "text").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    parsed = json.loads(raw)

    brief = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id, DailyBrief.day == target_day)
        .one_or_none()
    )
    if brief is None:
        brief = DailyBrief(user_id=user.id, day=target_day, model=cfg.anthropic_model_default,
                           readiness_summary="", training_prescription="")
        db.add(brief)
    brief.readiness_summary = parsed.get("readiness_summary", "")
    brief.training_prescription = parsed.get("training_prescription", "")
    brief.rationale = parsed.get("rationale")
    brief.flags = parsed.get("flags")
    # Clamped rather than trusted: the model occasionally returns a score outside
    # the range it was given, and an out-of-range value would break the colour
    # scale rather than merely being wrong.
    score = parsed.get("readiness_score")
    brief.readiness_score = (
        min(max(int(score), 0), 100) if isinstance(score, (int, float)) else None
    )
    planned = parsed.get("session_planned")
    brief.session_planned = planned if isinstance(planned, bool) else None
    brief.model = cfg.anthropic_model_default
    db.commit()
    db.refresh(brief)
    return brief
