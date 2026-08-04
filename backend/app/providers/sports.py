"""One sport vocabulary, shared by every provider.

Each source names sports differently — Oura passes its `activity` string through
raw, HealthKit sends `running`/`cycling`, Strava would add `Run`/`Ride`/
`VirtualRide`. All three land in the same `workouts.sport` column, and
`ai/data.py` feeds that column straight into the model's context. Without a
shared vocabulary the coach sees `running`, `Run`, and Oura's raw string as three
unrelated activities and cannot reason about training load across them.

Normalising is lossless: the provider's original payload is still kept in
`workouts.raw`.
"""

from __future__ import annotations

import logging
from typing import Literal, get_args

log = logging.getLogger(__name__)

Sport = Literal[
    "running",
    "trail_running",
    "cycling",
    "mtb",
    "indoor_cycling",
    "swimming",
    "rowing",
    "strength",
    "walking",
    "hiking",
    "yoga",
    "ski_nordic",
    "ski_alpine",
    "other",
]

SPORTS: tuple[str, ...] = get_args(Sport)

# Strava `sport_type` values. Use `sport_type`, not the deprecated `type`.
STRAVA_SPORT: dict[str, Sport] = {
    "Run": "running",
    "TrailRun": "trail_running",
    "VirtualRun": "running",
    "Ride": "cycling",
    "GravelRide": "cycling",
    "EBikeRide": "cycling",
    "MountainBikeRide": "mtb",
    "EMountainBikeRide": "mtb",
    "VirtualRide": "indoor_cycling",
    "Swim": "swimming",
    "Rowing": "rowing",
    "VirtualRow": "rowing",
    "Kayaking": "rowing",
    "WeightTraining": "strength",
    "Crossfit": "strength",
    "Workout": "strength",
    "Walk": "walking",
    "Hike": "hiking",
    "Yoga": "yoga",
    "NordicSki": "ski_nordic",
    "BackcountrySki": "ski_nordic",
    "AlpineSki": "ski_alpine",
    "Snowboard": "ski_alpine",
}

# Oura workout `activity` values, which are lowercase and free-form.
OURA_SPORT: dict[str, Sport] = {
    "running": "running",
    "trail_running": "trail_running",
    "jogging": "running",
    "cycling": "cycling",
    "mountain_biking": "mtb",
    "indoor_cycling": "indoor_cycling",
    "swimming": "swimming",
    "rowing": "rowing",
    "strength_training": "strength",
    "weightlifting": "strength",
    "walking": "walking",
    "hiking": "hiking",
    "yoga": "yoga",
    "cross_country_skiing": "ski_nordic",
    "skiing": "ski_alpine",
    "snowboarding": "ski_alpine",
}

# HealthKit values already arrive normalised (see HKWorkoutActivityType.coachName
# on the iOS side); this is here so ingest validates against the same set.
HEALTHKIT_SPORT: dict[str, Sport] = {s: s for s in SPORTS}


def normalize(value: str | None, mapping: dict[str, Sport]) -> Sport:
    """Map a provider's sport name onto the canonical set.

    Unknown values become "other" and are logged rather than dropped or passed
    through — passing through is what let three vocabularies coexist, and the log
    is how the long tail gets discovered.
    """
    if not value:
        return "other"
    key = value.strip()
    if key in mapping:
        return mapping[key]
    lowered = key.lower().replace(" ", "_").replace("-", "_")
    if lowered in mapping:
        return mapping[lowered]
    if lowered in SPORTS:
        return lowered  # type: ignore[return-value]
    log.info("unmapped sport %r — falling back to 'other'", value)
    return "other"
