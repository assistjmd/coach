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
                    f"Today is {target_day.isoformat()}. Based on the data below, produce a JSON "
                    f"object with keys: readiness_summary (string, 2-3 sentences), "
                    f"training_prescription (string, what to do today), rationale (string), "
                    f"flags (object with boolean keys like 'illness_risk', 'overreaching'). "
                    f"Return ONLY the JSON, no prose around it.\n\n"
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
    brief.model = cfg.anthropic_model_default
    db.commit()
    db.refresh(brief)
    return brief
