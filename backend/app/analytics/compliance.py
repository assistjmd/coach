"""Did the athlete do what the coach asked?

Inference is deliberately coarse. The prescription is prose — "3x8 at threshold,
not 4x10" — and parsing that into a target the logged workout can be checked
against is a research problem, not a feature. Pretending otherwise produces
confident wrong answers, and being told you skipped a session you actually did is
the worst outcome this feature can produce.

So the model emits `session_planned` alongside the prose, and inference only
answers a question it can actually answer: was a session asked for, and did one
happen. Anything finer is left to the athlete's own correction, which always
wins.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from app.models import DailyBrief

Status = Literal["complete", "partial", "missed", "rest", "unknown"]

# Below this share of a typical session, a logged workout reads as a token effort
# rather than the session — the athlete started and stopped. Above it, credit.
_PARTIAL_LOAD_RATIO = 0.5


def infer(
    brief: DailyBrief | None,
    day_load: float,
    typical_load: float,
) -> Status:
    """Compliance for one day.

    `typical_load` is the athlete's own recent median, not a fixed threshold: a
    session that counts as a full effort for one person is a warm-up for another.
    """
    if brief is None:
        # No brief means the coach never made a call, so there is nothing to
        # comply with. Distinct from a rest day, which is an actual instruction.
        return "unknown"

    trained = day_load > 0

    if brief.session_planned is False:
        # Rest was the instruction. Training anyway is not a failure — people
        # move on rest days — so this stays "rest" rather than becoming a miss.
        return "rest"

    if brief.session_planned is None:
        # Older briefs predate the field. Guessing a miss for every one of them
        # would fabricate a history of failure the athlete never had.
        return "complete" if trained else "unknown"

    if not trained:
        return "missed"
    if typical_load > 0 and day_load < typical_load * _PARTIAL_LOAD_RATIO:
        return "partial"
    return "complete"


def resolve(brief: DailyBrief | None) -> Status:
    """The status to show: the athlete's correction if they made one."""
    if brief is None:
        return "unknown"
    if brief.compliance_override:
        return brief.compliance_override  # type: ignore[return-value]
    return brief.compliance_auto or "unknown"  # type: ignore[return-value]


def day_key(d: date) -> str:
    return d.isoformat()
