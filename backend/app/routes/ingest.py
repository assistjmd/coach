from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import (
    BodyMetric,
    HeartRateSample,
    HRVReading,
    SleepSession,
    User,
    Workout,
)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


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
    metric: Literal["weight_kg", "body_fat_pct", "vo2_max", "resting_hr"]
    value: float
    measured_at: datetime


class HealthKitBatch(BaseModel):
    workouts: list[HKWorkout] = Field(default_factory=list)
    sleep: list[HKSleep] = Field(default_factory=list)
    hrv: list[HKHRV] = Field(default_factory=list)
    heart_rate: list[HKHeartRate] = Field(default_factory=list)
    body_metrics: list[HKBodyMetric] = Field(default_factory=list)


class IngestSummary(BaseModel):
    workouts: int
    sleep: int
    hrv: int
    heart_rate: int
    body_metrics: int


@router.post("/healthkit", response_model=IngestSummary)
def ingest_healthkit(
    batch: HealthKitBatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IngestSummary:
    src = "healthkit"

    written = IngestSummary(
        workouts=_upsert(
            db,
            Workout,
            "uq_workout_source_id",
            lambda w: w.source_record_id,
            batch.workouts,
            lambda w: dict(
                user_id=user.id,
                source=src,
                source_record_id=w.source_record_id,
                sport=w.sport,
                started_at=w.started_at,
                ended_at=w.ended_at,
                duration_s=w.duration_s,
                distance_m=w.distance_m,
                avg_hr=w.avg_hr,
                max_hr=w.max_hr,
            ),
        ),
        sleep=_upsert(
            db,
            SleepSession,
            "uq_sleep_source_id",
            lambda s: s.source_record_id,
            batch.sleep,
            lambda s: dict(
                user_id=user.id,
                source=src,
                source_record_id=s.source_record_id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                deep_s=s.deep_s,
                light_s=s.light_s,
                rem_s=s.rem_s,
                awake_s=s.awake_s,
            ),
        ),
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
    return written


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
