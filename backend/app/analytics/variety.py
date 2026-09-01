"""What the coach has already been prescribing, counted.

The coach was given ten days of prior sessions and prescribed the same easy run
fourteen days running anyway. The data was there and visible — a list of past
sessions is simply something a model can read past without noticing it is
repeating itself.

So this counts instead of listing. "You have prescribed an easy run on 11 of the
last 14 days" is a sentence that cannot be skimmed. The untouched movement
patterns matter as much: knowing what has been neglected is what turns "vary it"
from an instruction into a choice between named alternatives.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session

from app.dates import today_for
from app.models import DailyBrief, User

# Movement patterns, and the words that give them away. Coarse on purpose: this
# decides what to suggest next, not what counts as correct form.
_PATTERNS: dict[str, tuple[str, ...]] = {
    "squat": ("squat", "lunge", "step-up", "step up", "leg press", "split squat"),
    "hinge": ("deadlift", "rdl", "romanian", "hip thrust", "good morning", "swing"),
    "horizontal push": ("bench", "push-up", "push up", "chest press", "dip"),
    "vertical push": ("overhead press", "shoulder press", "military press", "handstand"),
    "horizontal pull": ("row", "face pull", "inverted row"),
    "vertical pull": ("pull-up", "pull up", "chin-up", "chin up", "lat pulldown", "pulldown"),
    "carry": ("carry", "farmer", "suitcase"),
    "core": ("plank", "dead bug", "pallof", "hollow", "ab wheel", "crunch"),
    "easy run": ("easy run", "conversational", "zone 2 run", "recovery run"),
    "hard run": ("interval", "tempo", "threshold", "fartlek", "strides", "hill repeat", "track"),
    "long run": ("long run",),
    "ride": ("ride", "bike", "cycling", "spin"),
    "mobility": ("stretch", "mobility", "yoga", "foam roll"),
    "walk": ("walk", "hike", "incline treadmill"),
}

# Warm-ups and cool-downs repeat by design and are not the variety anyone means.
_INCIDENTAL = ("walk", "mobility")

# Uses of one exercise within the window before it counts as over-reached-for.
# Three in a fortnight is a staple; it is the fourth that starts to feel like
# the app has run out of ideas.
_OVERUSED_AT = 3


@dataclass
class Variety:
    # Exercise or session type -> times prescribed in the window.
    recently_prescribed: dict[str, int] = field(default_factory=dict)
    # Pattern -> days since it was last asked for. Absent means never, in window.
    days_since_pattern: dict[str, int] = field(default_factory=dict)
    untouched_patterns: list[str] = field(default_factory=list)
    # The single thing repeated most, with its count. The headline number.
    most_repeated: str | None = None
    most_repeated_count: int = 0
    days_examined: int = 0
    # Yesterday's session, verbatim, and how many days running it has been the
    # same. Counting across a fortnight misses this entirely: a plan that
    # alternates two sessions in four-day blocks spreads fine on paper and
    # feels like Groundhog Day to the person doing it.
    yesterdays_session: list[str] = field(default_factory=list)
    consecutive_identical_days: int = 0
    # Exercises reached for repeatedly across the window, worst first. Naming
    # only the single most-repeated one hid the real pattern: a coach cycling
    # one push-day template will show five separate exercises at five uses
    # each, and the top-one summary reports that as a single repetition.
    overused: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def classify(text: str) -> str | None:
    """The movement pattern an item belongs to, if any is recognisable."""
    lowered = text.lower()
    for pattern, words in _PATTERNS.items():
        if any(w in lowered for w in words):
            return pattern
    return None


def _headline(text: str) -> str:
    """The exercise, without the prescription attached.

    "Back squat, 4x5 — controlled descent" and "Back squat 3x8" are the same
    movement, and counting them separately would report variety that is not
    there.
    """
    lowered = text.lower()
    # Durations first, and before the dash split, or "35–40 min easy run" is cut
    # at the en dash and counted as the exercise "35".
    lowered = re.sub(r"\b\d+\s*[-–—]?\s*\d*\s*(min|minute|hour|hr|km|mi|mile)s?\b", "", lowered)
    lowered = re.sub(r"\b\d+\s*x\s*\d+\b", "", lowered)
    head = re.split(r"[,(]|\s[—–]\s", lowered, maxsplit=1)[0]
    return " ".join(head.split())[:40].strip()


def summarise(db: Session, user: User, days: int = 14) -> Variety:
    start = today_for(user) - timedelta(days=days)
    briefs = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id, DailyBrief.day >= start)
        .order_by(DailyBrief.day.desc())
        .all()
    )
    if not briefs:
        return Variety(days_examined=0)

    today = today_for(user)
    counts: Counter[str] = Counter()
    last_seen: dict[str, int] = {}

    for b in briefs:
        age = (today - b.day).days
        for item in (b.session_items or []):
            if not isinstance(item, dict) or not item.get("text"):
                continue
            name = _headline(item["text"])
            pattern = classify(item["text"])
            if pattern in _INCIDENTAL:
                # A five minute walk before every session is not the repetition
                # anyone is complaining about.
                continue
            if name:
                counts[name] += 1
            if pattern is not None:
                last_seen.setdefault(pattern, age)

    trained = {p for p in _PATTERNS if p not in _INCIDENTAL}
    top = counts.most_common(1)

    # `briefs` is newest-first. Walk it while the session is unchanged.
    def signature(b) -> tuple[str, ...]:
        return tuple(
            _headline(i["text"])
            for i in (b.session_items or [])
            if isinstance(i, dict) and i.get("text")
            and classify(i["text"]) not in _INCIDENTAL
        )

    run = 0
    if briefs:
        first = signature(briefs[0])
        if first:
            run = 1
            for b in briefs[1:]:
                if signature(b) == first:
                    run += 1
                else:
                    break

    previous = briefs[1] if len(briefs) > 1 else None
    return Variety(
        recently_prescribed=dict(counts.most_common(12)),
        days_since_pattern=last_seen,
        untouched_patterns=sorted(trained - set(last_seen)),
        most_repeated=top[0][0] if top else None,
        most_repeated_count=top[0][1] if top else 0,
        days_examined=len(briefs),
        overused={k: v for k, v in counts.most_common() if v >= _OVERUSED_AT},
        yesterdays_session=[
            i["text"] for i in ((previous.session_items or []) if previous else [])
            if isinstance(i, dict) and i.get("text")
        ],
        consecutive_identical_days=run,
    )
