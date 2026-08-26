"""Reconcile sleep into one row per night per device.

Apple Health records a night as dozens of stage samples, and the client reads
them through an anchored query that returns whichever ones are new. So a night
arrives in pieces, across several syncs, and each piece was landing as its own
"night" keyed on the first fragment it happened to contain. Five identical rows
for one night, and stage totals summed across re-reports until a 7.6 hour window
claimed 15.2 hours of sleep.

Three relationships between two sleep rows, and they need different answers:

- **Different sources** never merge. An Oura night and an Apple Watch night are
  two devices describing one sleep; summing them invents a fourteen-hour night.
- **Same source, overlapping** is the same night reported twice. Take the union
  of the span and the *maximum* of each stage, never the sum — the second report
  is a better view of the same sleep, not additional sleep.
- **Same source, adjacent** (a gap under 90 minutes) is two fragments of one
  night. Here the stages do add, because they describe different parts.

A physical backstop sits on top: time asleep cannot exceed time in bed. Anything
still claiming otherwise after merging is double-counted, and is scaled back to
the window it actually occupies.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import SleepSession

log = logging.getLogger(__name__)

# Matches HealthKitManager.sleepGap: long enough to absorb a trip to the
# bathroom, short enough that an afternoon nap stays its own session.
_GAP = timedelta(minutes=90)

_STAGES = ("deep_s", "light_s", "rem_s", "awake_s")


def _relationship(a: SleepSession, b: SleepSession) -> str:
    """How `b` relates to `a`, where `a` starts no later than `b`."""
    if a.source != b.source:
        return "separate"
    if b.started_at < a.ended_at:
        return "overlapping"
    if b.started_at - a.ended_at <= _GAP:
        return "adjacent"
    return "separate"


def combine(keeper: SleepSession, other: SleepSession, how: str) -> None:
    keeper.ended_at = max(keeper.ended_at, other.ended_at)
    for field in _STAGES:
        mine, theirs = getattr(keeper, field), getattr(other, field)
        if mine is None:
            setattr(keeper, field, theirs)
        elif theirs is not None:
            # The distinction that fixes the 15-hour night.
            setattr(keeper, field, max(mine, theirs) if how == "overlapping" else mine + theirs)
    # Efficiency is a percentage and cannot be summed. Keep whichever exists
    # rather than averaging into a figure describing no measurement taken.
    keeper.efficiency = keeper.efficiency or other.efficiency


def clamp_to_window(row: SleepSession) -> bool:
    """Scale stages back when they claim more sleep than the night contained.

    A last line of defence rather than the main fix: if this fires, something
    upstream double-counted, and the honest response is a night bounded by the
    hours it actually occupied. Returns whether anything changed.
    """
    span = int((row.ended_at - row.started_at).total_seconds())
    if span <= 0:
        return False
    total = sum(getattr(row, f) or 0 for f in _STAGES)
    if total <= span:
        return False

    ratio = span / total
    for field in _STAGES:
        value = getattr(row, field)
        if value:
            setattr(row, field, int(value * ratio))
    log.warning(
        "sleep %s claimed %ds of stages inside a %ds window; scaled",
        row.id, total, span,
    )
    return True


def collapse_nights(db: Session, user_id) -> int:
    """Merge duplicate and fragmented sleep. Returns rows removed."""
    rows = (
        db.query(SleepSession)
        .filter(SleepSession.user_id == user_id)
        .order_by(SleepSession.started_at)
        .all()
    )
    if not rows:
        return 0

    removed = 0
    # One keeper per source, since rows from different devices interleave in a
    # single start-ordered pass.
    keepers: dict[str, SleepSession] = {}

    for row in rows:
        keeper = keepers.get(row.source)
        if keeper is None:
            keepers[row.source] = row
            continue
        how = _relationship(keeper, row)
        if how == "separate":
            keepers[row.source] = row
            continue
        combine(keeper, row, how)
        db.delete(row)
        removed += 1

    changed = removed
    for row in rows:
        if row in db.deleted:
            continue
        if clamp_to_window(row):
            changed += 1

    if changed:
        db.commit()
        log.info("merged %d sleep rows for user %s", removed, user_id)
    return removed
