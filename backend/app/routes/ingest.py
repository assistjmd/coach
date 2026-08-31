import logging
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.analytics.dedupe import (
    better,
    collapse_existing,
    find_duplicate,
    record_contributor,
)
from app.analytics.sleep_repair import combine, clamp_to_window, collapse_nights
from app.auth.deps import current_user
from app.jobs.daily_brief import (
    _MEANINGFUL_MINUTES,
    refresh_after_activity,
    regenerate_if_sleep_landed,
)
from app.db import get_db
from app.models import (
    BodyMetric,
    HeartRateSample,
    HRVReading,
    SleepSession,
    User,
    Workout,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])

# Generous against the client's 400-record chunks, tight against an attacker.
_MAX_BATCH = 2000


class HKWorkout(BaseModel):
    source_record_id: str
    sport: str | None = None
    started_at: datetime
    ended_at: datetime
    duration_s: int
    distance_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None


class HKSleep(BaseModel):
    source_record_id: str
    started_at: datetime
    ended_at: datetime
    deep_s: int | None = None
    light_s: int | None = None
    rem_s: int | None = None
    awake_s: int | None = None


class HKHRV(BaseModel):
    measured_at: datetime
    sdnn_ms: float


class HKHeartRate(BaseModel):
    measured_at: datetime
    bpm: int
    context: Literal["rest", "active", "sleep"] | None = None


class HKBodyMetric(BaseModel):
    # Daily activity totals are included because every other metric here needs an
    # Apple Watch. An iPhone-only user has steps and nothing else, so without
    # these the app ingests literally nothing from Apple Health while Health
    # itself is visibly full of data.
    metric: Literal[
        "weight_kg",
        "body_fat_pct",
        "vo2_max",
        "resting_hr",
        "steps",
        "active_energy_kcal",
        "exercise_minutes",
    ]
    value: float
    measured_at: datetime


class HealthKitBatch(BaseModel):
    # Bounded per batch. The client already chunks at 400 records, so these
    # ceilings only bite on a malformed or hostile caller — for whom an
    # unbounded list is a cheap way to exhaust memory and fill the database.
    workouts: list[HKWorkout] = Field(default_factory=list, max_length=_MAX_BATCH)
    sleep: list[HKSleep] = Field(default_factory=list, max_length=_MAX_BATCH)
    hrv: list[HKHRV] = Field(default_factory=list, max_length=_MAX_BATCH)
    heart_rate: list[HKHeartRate] = Field(default_factory=list, max_length=_MAX_BATCH)
    body_metrics: list[HKBodyMetric] = Field(default_factory=list, max_length=_MAX_BATCH)


class IngestSummary(BaseModel):
    workouts: int
    sleep: int
    hrv: int
    heart_rate: int
    body_metrics: int


@router.post("/repair-duplicates")
def repair_duplicates(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Collapse duplicate sessions already stored for this athlete.

    The guards on the write paths only protect new data. Anyone who connected
    Strava and Apple Health before those landed has a history where one session
    appears several times, and the coach reads that history — so the inflated
    load keeps driving the prescription until it is cleaned up.

    Also merges fragmented sleep. Apple Health records a night as dozens of
    stage samples, and rows written before that was fixed are still one session
    per fragment — which is nonsense for the coach to reason about and, at
    hundreds of rows, most of the model context it is sent.

    Safe to run repeatedly: with nothing left to merge it removes nothing.
    """
    workouts = collapse_existing(db, user.id)
    nights = collapse_nights(db, user.id)
    return {"removed": workouts, "sleep_merged": nights}


@router.post("/healthkit", response_model=IngestSummary)
def ingest_healthkit(
    batch: HealthKitBatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IngestSummary:
    src = "healthkit"

    written = IngestSummary(
        workouts=_ingest_workouts(db, user, batch.workouts, src),
        sleep=_ingest_sleep(db, user, batch.sleep, src),
        # HealthKit reports SDNN; Oura reports rMSSD. Separate columns, never coerced.
        hrv=_upsert(
            db,
            HRVReading,
            "uq_hrv_measured_at",
            lambda h: h.measured_at,
            batch.hrv,
            lambda h: dict(
                user_id=user.id, source=src, measured_at=h.measured_at, sdnn_ms=h.sdnn_ms
            ),
        ),
        heart_rate=_upsert(
            db,
            HeartRateSample,
            "uq_hr_measured_at",
            lambda hr: hr.measured_at,
            batch.heart_rate,
            lambda hr: dict(
                user_id=user.id,
                source=src,
                measured_at=hr.measured_at,
                bpm=hr.bpm,
                context=hr.context,
            ),
        ),
        body_metrics=_upsert(
            db,
            BodyMetric,
            "uq_body_metric_measured_at",
            lambda bm: (bm.metric, bm.measured_at),
            batch.body_metrics,
            lambda bm: dict(
                user_id=user.id,
                source=src,
                metric=bm.metric,
                value=bm.value,
                measured_at=bm.measured_at,
            ),
        ),
    )
    db.commit()

    # A brief written on the deadline without last night's sleep is rewritten
    # once, now that it has arrived. Cheap to check and it stops someone who
    # opens the app at 8am from reading a day built on the wrong night.
    # A workout long enough to matter makes the morning's headline wrong: it
    # still says "get your run in" to someone who ran at lunchtime.
    if any((w.duration_s or 0) >= _MEANINGFUL_MINUTES * 60 for w in batch.workouts):
        try:
            refresh_after_activity(db, user)
        except Exception:
            log.exception("brief refresh failed for user %s", user.id)

    if batch.sleep:
        try:
            regenerate_if_sleep_landed(db, user)
        except Exception:
            # Ingest succeeded; the brief is a bonus. Failing the upload because
            # a model call went wrong would lose the health data too.
            log.exception("brief regeneration failed for user %s", user.id)

    return written


def _ingest_workouts(db: Session, user: User, incoming, src: str) -> int:
    """Write HealthKit workouts, collapsing sessions we already have.

    Apple Health holds one HKWorkout per app that wrote it, so a single lift can
    appear four times over — Garmin Connect, the Strava app, the Watch — each
    with its own UUID. Those are indistinguishable to a per-source unique
    constraint, and every extra copy inflated the day's training load.

    Ordered by start time so that when a batch contains duplicates of each
    other, the first is stored and the rest fold into it.
    """
    written = 0
    for w in sorted(incoming, key=lambda x: x.started_at):
        existing = find_duplicate(db, user.id, w.started_at, w.ended_at)
        if existing is not None:
            if existing.source == src and existing.source_record_id == w.source_record_id:
                # Genuinely the same row; refresh the fields that can change.
                existing.avg_hr = w.avg_hr or existing.avg_hr
                existing.max_hr = w.max_hr or existing.max_hr
                continue
            candidate = Workout(
                source=src, sport=w.sport, avg_hr=w.avg_hr,
                max_hr=w.max_hr, distance_m=w.distance_m,
            )
            record_contributor(existing, src)
            if better(candidate, existing):
                existing.source = src
                existing.source_record_id = w.source_record_id
                existing.sport = w.sport or existing.sport
                existing.started_at = w.started_at
                existing.ended_at = w.ended_at
                existing.duration_s = w.duration_s
                existing.distance_m = w.distance_m or existing.distance_m
                existing.avg_hr = w.avg_hr or existing.avg_hr
                existing.max_hr = w.max_hr or existing.max_hr
            record_contributor(existing, existing.source)
            continue

        db.add(Workout(
            user_id=user.id,
            source=src,
            contributing_sources=[src],
            source_record_id=w.source_record_id,
            sport=w.sport,
            started_at=w.started_at,
            ended_at=w.ended_at,
            duration_s=w.duration_s,
            distance_m=w.distance_m,
            avg_hr=w.avg_hr,
            max_hr=w.max_hr,
        ))
        db.flush()
        written += 1
    return written


def _ingest_sleep(db: Session, user: User, incoming, src: str) -> int:
    """Write sleep, reconciling against nights we already hold.

    A night reaches us in pieces: the client reads Apple Health through an
    anchored query, so each sync carries whichever stage samples are new, and
    the row was keyed on whichever fragment happened to come first. Every sync
    that caught part of a night therefore created another night.

    Matching on overlap instead, and merging with the same rules the repair job
    uses, so ingestion and repair cannot disagree about what a night is.
    """
    written = 0
    for s in sorted(incoming, key=lambda x: x.started_at):
        candidate = SleepSession(
            user_id=user.id,
            source=src,
            source_record_id=s.source_record_id,
            started_at=s.started_at,
            ended_at=s.ended_at,
            deep_s=s.deep_s,
            light_s=s.light_s,
            rem_s=s.rem_s,
            awake_s=s.awake_s,
        )
        existing = _overlapping_night(db, user, src, s.started_at, s.ended_at)
        if existing is not None:
            how = (
                "overlapping"
                if candidate.started_at < existing.ended_at
                and candidate.ended_at > existing.started_at
                else "adjacent"
            )
            earlier, later = (
                (existing, candidate)
                if existing.started_at <= candidate.started_at
                else (candidate, existing)
            )
            if earlier is candidate:
                # The new report starts first, so it defines the night's start.
                existing.started_at = candidate.started_at
            combine(existing, later if later is candidate else earlier, how)
            clamp_to_window(existing)
            continue

        db.add(candidate)
        db.flush()
        clamp_to_window(candidate)
        written += 1
    return written


def _overlapping_night(db: Session, user: User, src: str, start, end):
    """A night from the same source that this one continues or restates."""
    window = timedelta(minutes=90)
    return (
        db.query(SleepSession)
        .filter(
            SleepSession.user_id == user.id,
            SleepSession.source == src,
            SleepSession.started_at <= end + window,
            SleepSession.ended_at >= start - window,
        )
        .order_by(SleepSession.started_at)
        .first()
    )


def _upsert(db: Session, model, constraint: str, key, items: list, to_row) -> int:
    """Insert `items`, skipping anything already stored. Returns rows written.

    Two kinds of duplicate have to be handled, and only one of them is the
    database's job:

    - Across requests — HealthKit re-sends overlapping windows on every observer
      firing — handled by ON CONFLICT against the unique constraint.
    - Within a single batch, which the constraint cannot see because those rows
      arrive in the same statement. Collapsed here by `key` first.

    The count is rows actually written, not rows submitted, so a replayed batch
    honestly reports zero rather than claiming it ingested everything again.
    """
    if not items:
        return 0
    deduped = {key(item): to_row(item) for item in items}
    stmt = (
        pg_insert(model)
        .values(list(deduped.values()))
        .on_conflict_do_nothing(constraint=constraint)
        .returning(model.id)
    )
    return len(db.execute(stmt).scalars().all())
