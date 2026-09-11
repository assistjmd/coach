"""Collapse the same training session arriving from several sources.

One strength session can reach us five times: Strava's API sends it, and Garmin
Connect, the Strava iOS app and the Apple Watch each write their own HKWorkout
into Apple Health, every one with a different UUID. Nothing about the identifiers
reveals they describe a single session, so `uq_workout_source_id` — which is a
per-source guard — lets all five through.

The cost is not cosmetic. `daily_loads` sums whatever is in the table, so five
copies of a 41-minute lift produce roughly five times the real training load,
and the coach then prescribes recovery for a week that never happened.

Two sessions are treated as one when they belong to the same athlete and their
time windows genuinely overlap. Adjacency is deliberately not enough: a brick
session is a bike immediately followed by a run, and those are two real
workouts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Workout

log = logging.getLogger(__name__)

# Share of the longer session that must overlap before we call it a duplicate.
# Different apps clock the same lift at 41 and 42 minutes and may start a minute
# apart, so an exact match would catch nothing. Set high enough that a bike
# followed by a run is never merged.
_OVERLAP_RATIO = 0.75

# How trustworthy each source's record is when two describe the same session.
# Strava and Garmin come from the device that recorded the session and carry
# sport, distance and heart rate. A generic HealthKit row is often a copy
# written by another app, with less detail.
_SOURCE_RANK = {
    "garmin": 4,
    "wahoo": 4,
    "strava": 3,
    "google_health": 2,
    "oura": 2,
    "healthkit": 1,
}


def _aware(value: datetime | str) -> datetime:
    """Normalise whatever a caller has to an aware datetime.

    Provider payloads carry ISO strings — Oura passes `bedtime_start` straight
    through, Google Health sends RFC-3339 — and SQLAlchemy only coerces those at
    insert time, so they arrive here as text. Database rows come back naive or
    aware depending on the driver. Both have to work, since this decides whether
    a session is a duplicate.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    a_start, a_end = _aware(a_start), _aware(a_end)
    b_start, b_end = _aware(b_start), _aware(b_end)

    overlap = (min(a_end, b_end) - max(a_start, b_start)).total_seconds()
    if overlap <= 0:
        return False
    longer = max(
        (a_end - a_start).total_seconds(), (b_end - b_start).total_seconds()
    )
    if longer <= 0:
        return False
    return overlap / longer >= _OVERLAP_RATIO


def compatible_sports(a: str | None, b: str | None) -> bool:
    generic = {None, "other", "workout", "unknown"}
    return a in generic or b in generic or a == b


def _completeness(w: Workout) -> tuple[int, int]:
    """How much a record is worth keeping.

    Heart rate outranks source, because it is what decides whether the session
    is scored by TRIMP or by the duration estimate. A HealthKit row with heart
    rate beats a Strava row without it.
    """
    signals = sum(
        1 for v in (w.avg_hr, w.max_hr, w.distance_m, w.sport) if v is not None
    )
    return (1 if w.avg_hr else 0, signals + _SOURCE_RANK.get(w.source, 0))


def find_duplicate(
    db: Session, user_id, started_at: datetime, ended_at: datetime,
    sport: str | None = None,
) -> Workout | None:
    """An existing workout describing the same session, if there is one.

    The candidate query is deliberately wide — a day either side — and the
    overlap test does the real work in Python. Doing this in SQL would need an
    interval extension we do not have.
    """
    from datetime import timedelta

    started_at, ended_at = _aware(started_at), _aware(ended_at)
    candidates = (
        db.query(Workout)
        .filter(
            Workout.user_id == user_id,
            Workout.started_at >= started_at - timedelta(days=1),
            Workout.started_at <= started_at + timedelta(days=1),
        )
        .all()
    )
    for c in candidates:
        if compatible_sports(sport, c.sport) and _overlaps(
            started_at, ended_at, c.started_at, c.ended_at
        ):
            return c
    return None


def better(incoming: Workout, existing: Workout) -> bool:
    """Whether the incoming record should replace the one already stored."""
    return _completeness(incoming) > _completeness(existing)


def record_contributor(kept: Workout, source: str) -> None:
    """Note that `source` also saw this session.

    Kept whichever record wins, so the surviving row carries the full list of
    devices that agreed. Sorted and deduplicated because this is read back and
    shown to the athlete, and an order that shifts between syncs would look
    like the data changed when it did not.
    """
    seen = set(kept.contributing_sources or [])
    seen.add(kept.source)
    seen.add(source)
    kept.contributing_sources = sorted(seen)


def collapse_existing(db: Session, user_id) -> int:
    """Merge duplicates already in the table.

    Needed because the guard above only protects future writes, and an athlete
    who connected several sources already has inflated history — which is what
    the coach reads.

    Returns the number of rows removed.
    """
    workouts = (
        db.query(Workout)
        .filter(Workout.user_id == user_id)
        .order_by(Workout.started_at)
        .all()
    )
    kept: list[Workout] = []
    removed = 0

    for w in workouts:
        match = next(
            (k for k in kept if compatible_sports(w.sport, k.sport)
             and _overlaps(w.started_at, w.ended_at, k.started_at, k.ended_at)),
            None,
        )
        if match is None:
            kept.append(w)
            continue
        # Keep whichever record carries more signal, drop the other — but keep
        # the fact that both saw it.
        if better(w, match):
            record_contributor(w, match.source)
            for s in (match.contributing_sources or []):
                record_contributor(w, s)
            kept[kept.index(match)] = w
            db.delete(match)
        else:
            record_contributor(match, w.source)
            for s in (w.contributing_sources or []):
                record_contributor(match, s)
            db.delete(w)
        removed += 1

    if removed:
        db.commit()
        log.info("collapsed %d duplicate workouts for user %s", removed, user_id)
    return removed
