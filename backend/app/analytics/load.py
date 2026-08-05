"""Training load.

Banister TRIMP, which weights time by how hard the heart was working:

    TRIMP = minutes × HRr × 0.64 × e^(1.92 × HRr)
    HRr   = (HR_avg − HR_rest) / (HR_max − HR_rest)

The exponential is the point. An hour easy and an hour at threshold are not the
same stimulus, and anything linear in duration says they are — which for a hybrid
athlete makes the whole metric useless.

Two honest limitations, both surfaced rather than hidden:

1. The 1.92 coefficient is the male form; the female form is 1.67. We do not
   collect sex, so every load uses 1.92. It biases the absolute number, not the
   shape of the trend, and the trend is what the calendar shows.
2. Plenty of sessions have no heart rate — a treadmill run with no strap, a lift
   logged by hand. Those fall back to duration × a per-sport intensity guess, and
   the method is returned alongside the number so the UI can render an estimate
   differently from a measurement. A bar you can't trust must not look like one
   you can.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import BodyMetric, Workout

Method = Literal["trimp", "duration"]

# Fallback intensity per sport, used only when heart rate is missing. These are
# deliberately coarse: they exist so a strength session and a long ride are not
# scored identically, not to be physiologically precise.
_SPORT_INTENSITY: dict[str, float] = {
    "running": 1.0,
    "trail_running": 1.0,
    "cycling": 0.8,
    "mtb": 0.9,
    "indoor_cycling": 0.9,
    "swimming": 1.0,
    "rowing": 1.0,
    "strength": 0.7,
    "walking": 0.3,
    "hiking": 0.5,
    "yoga": 0.3,
    "ski_nordic": 1.0,
    "ski_alpine": 0.6,
    "other": 0.6,
}

# Used when the athlete has no resting-HR or max-HR history yet. Population
# averages, replaced by that person's own numbers as soon as we have them.
_DEFAULT_HR_REST = 60
_DEFAULT_HR_MAX = 190


@dataclass(frozen=True)
class HRProfile:
    """The two anchors TRIMP needs, derived from the athlete's own data."""

    rest: int
    max: int


def hr_profile(db: Session, user_id, as_of: date | None = None) -> HRProfile:
    """Resting and max heart rate for one athlete.

    Resting HR is the median of recent measurements rather than the minimum: a
    single artefactually low reading would otherwise permanently widen the
    denominator and deflate every load that follows.

    Max HR is the highest ever observed in a workout, not an age formula. We
    don't know their age, and an observed max is a fact where 220−age is a
    population guess that is wrong for most individuals.
    """
    since = datetime.now(timezone.utc) - timedelta(days=90)

    resting_values = [
        row[0]
        for row in db.query(BodyMetric.value)
        .filter(
            BodyMetric.user_id == user_id,
            BodyMetric.metric == "resting_hr",
            BodyMetric.measured_at >= since,
        )
        .order_by(BodyMetric.value)
        .all()
    ]
    rest = (
        int(resting_values[len(resting_values) // 2])
        if resting_values
        else _DEFAULT_HR_REST
    )

    observed_max = (
        db.query(func.max(Workout.max_hr)).filter(Workout.user_id == user_id).scalar()
    )
    peak = int(observed_max) if observed_max else _DEFAULT_HR_MAX

    # Guard the denominator. A corrupt pair (max at or below rest) would divide
    # by zero or invert the scale; falling back is better than emitting nonsense.
    if peak - rest < 20:
        rest, peak = _DEFAULT_HR_REST, _DEFAULT_HR_MAX
    return HRProfile(rest=rest, max=peak)


def workout_load(workout: Workout, hr: HRProfile) -> tuple[float, Method]:
    """Load for one session, and which method produced it."""
    minutes = (workout.duration_s or 0) / 60
    if minutes <= 0:
        return 0.0, "duration"

    avg_hr = workout.avg_hr
    if avg_hr:
        ratio = (avg_hr - hr.rest) / (hr.max - hr.rest)
        # Clamp: a warm-up-only session can average below resting, and a bad
        # sample can exceed the observed max. Both would otherwise produce a
        # negative or explosive load.
        ratio = min(max(ratio, 0.0), 1.0)
        return minutes * ratio * 0.64 * math.exp(1.92 * ratio), "trimp"

    intensity = _SPORT_INTENSITY.get(workout.sport or "other", 0.6)
    # Scaled to land in roughly the same range as a TRIMP score for the same
    # session, so a chart mixing both methods is not visually discontinuous.
    return minutes * intensity * 0.9, "duration"


def daily_loads(
    db: Session, user_id, start: date, end: date
) -> dict[date, tuple[float, Method]]:
    """Total load per calendar day, and whether any of it was estimated.

    A day is marked "duration" if *any* of its sessions lacked heart rate: the
    total is then partly a guess, and saying so is the honest signal.
    """
    workouts = (
        db.query(Workout)
        .filter(
            Workout.user_id == user_id,
            func.date(Workout.started_at) >= start,
            func.date(Workout.started_at) <= end,
        )
        .all()
    )
    hr = hr_profile(db, user_id)

    out: dict[date, tuple[float, Method]] = {}
    for w in workouts:
        day = w.started_at.date()
        load, method = workout_load(w, hr)
        prev_load, prev_method = out.get(day, (0.0, "trimp"))
        combined: Method = "duration" if "duration" in (prev_method, method) else "trimp"
        out[day] = (prev_load + load, combined)
    return {d: (round(v, 1), m) for d, (v, m) in out.items()}
