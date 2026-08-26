"""Merge sleep fragments already stored into whole nights.

Apple Health records a night as dozens of short stage samples. The ingest path
used to key each one separately, so a single night landed as thirty or more
"sleep sessions". Ingestion was fixed in build 16; the rows written before it
were not, and they are what the coach reads.

Two costs, and the second is the one that bites:

1. The brief sees hundreds of eight-minute sleeps instead of fourteen nights.
2. Every one of them is serialised into the model context. One tester's daily
   payload had grown to 41,000 tokens, resent in full on every chat message.

Same 90-minute clustering rule as the client uses, so repaired history and new
data describe nights the same way.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import SleepSession

log = logging.getLogger(__name__)

# Matches HealthKitManager.sleepGap. Long enough to absorb a trip to the
# bathroom, short enough that an afternoon nap stays its own session.
_GAP = timedelta(minutes=90)


def _sum(*values) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def collapse_nights(db: Session, user_id) -> int:
    """Merge fragmented sleep rows. Returns the number of rows removed."""
    rows = (
        db.query(SleepSession)
        .filter(SleepSession.user_id == user_id)
        .order_by(SleepSession.started_at)
        .all()
    )
    if not rows:
        return 0

    removed = 0
    keeper = rows[0]
    for row in rows[1:]:
        # Sources are kept apart: an Oura night and an Apple Watch night
        # describe the same sleep from two devices, and merging them would sum
        # the stages into a fourteen-hour night.
        same_source = row.source == keeper.source
        contiguous = row.started_at - keeper.ended_at <= _GAP

        if same_source and contiguous:
            keeper.ended_at = max(keeper.ended_at, row.ended_at)
            keeper.deep_s = _sum(keeper.deep_s, row.deep_s)
            keeper.light_s = _sum(keeper.light_s, row.light_s)
            keeper.rem_s = _sum(keeper.rem_s, row.rem_s)
            keeper.awake_s = _sum(keeper.awake_s, row.awake_s)
            # Efficiency is a percentage and cannot be summed. The keeper's own
            # value is kept, and a fragment's is dropped rather than averaged
            # into something that describes no measurement that was taken.
            keeper.efficiency = keeper.efficiency or row.efficiency
            db.delete(row)
            removed += 1
        else:
            keeper = row

    if removed:
        db.commit()
        log.info("merged %d sleep fragments for user %s", removed, user_id)
    return removed
