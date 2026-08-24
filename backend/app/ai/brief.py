"""Daily-brief generation. Run nightly per user."""

from __future__ import annotations

import json
from datetime import date

import anthropic
from sqlalchemy.orm import Session

from app.ai.data import gather_recent, user_profile
from app.ai.prompts import SYSTEM_PROMPT, user_context_block
from app.config import settings
from app.dates import today_for
from app.models import DailyBrief, User


def generate_daily_brief(db: Session, user: User, day: date | None = None) -> DailyBrief:
    cfg = settings()
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    snap = gather_recent(db, user, days=14)
    profile = user_profile(user, snap.get("goal"))
    # The athlete's today. On UTC infrastructure date.today() rolls over
    # mid-evening for anyone west of Greenwich, and a brief dated tomorrow
    # never shows up on the day it was written for.
    target_day = day or today_for(user)

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
                    f"  session_items - array of 1-8 objects. A lifting day needs "
                    f"more of them than a run: warm-up, each exercise as its own "
                    f"item, and a finisher. Name the exercise in every item - "
                    f"'Romanian deadlift, 3x8', never 'accessory work'. Each has 'id' (a "
                    f"short lowercase slug, no spaces) and 'text' (one concrete "
                    f"thing to do, under 60 characters). These become checkboxes, "
                    f"so each must be a single action the athlete can tick off - "
                    f"'3x8 at threshold', not 'do your session'. Split warm-up, "
                    f"main set and cool-down into separate items. On a rest day "
                    f"return an empty array.\n"
                    f"    Check recent_sessions in the data before choosing "
                    f"exercises. Do not prescribe the same lift two sessions "
                    f"running unless they are mid-progression on it or they "
                    f"skipped it last time.\n"
                    f"    When an item has a countable prescription, also include "
                    f"integer 'sets' and 'reps' so the athlete can log what they "
                    f"actually did against it. Reps means repetitions for lifting, "
                    f"or intervals for conditioning. Omit both for anything not "
                    f"countable - a 20 minute easy run has neither. Never invent "
                    f"numbers to fill the fields.\n"
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
    brief.session_items = clean_items(parsed.get("session_items"))
    brief.model = cfg.anthropic_model_default
    db.commit()
    db.refresh(brief)
    return brief


_MAX_SESSION_ITEMS = 8


def clean_items(raw: object) -> list[dict[str, str]] | None:
    """Validate the model's checkbox items before they reach the database.

    Shared with `ai/adjust.py`: a revised session has to satisfy exactly the
    same rules as a fresh one, and two validators would drift.

    Ids become the key the athlete's ticks are stored against, so a duplicate or
    missing id would make two checkboxes share one tick. Anything malformed is
    dropped rather than stored — a checkbox that behaves oddly is worse than one
    that isn't there.
    """
    if not isinstance(raw, list):
        return None
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    # Eight, not five. A lifting day is a warm-up, three or four named
    # exercises and a finisher; capping at five silently truncated the
    # accessory work and made every session look thinner than prescribed.
    for entry in raw[:_MAX_SESSION_ITEMS]:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id") or "").strip().lower().replace(" ", "_")
        text = str(entry.get("text") or "").strip()
        if not item_id or not text or item_id in seen:
            continue
        seen.add(item_id)
        item: dict[str, object] = {"id": item_id, "text": text[:120]}
        # Sets and reps are optional and often absent — a 20 minute easy run has
        # neither. Only kept when they are sane positive integers, so a model
        # hallucinating "sets": "a few" cannot reach the UI as a broken stepper.
        for field in ("sets", "reps"):
            value = entry.get(field)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and 1 <= int(value) <= 200:
                item[field] = int(value)
        out.append(item)
    return out or None
