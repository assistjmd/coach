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

    if batch.workouts:
        stmt = pg_insert(Workout).values(
            [
                dict(
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
                )
                for w in batch.workouts
            ]
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_workout_source_id"))

    if batch.sleep:
        stmt = pg_insert(SleepSession).values(
            [
                dict(
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
                for s in batch.sleep
            ]
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_sleep_source_id"))

    for h in batch.hrv:
        db.add(HRVReading(user_id=user.id, source=src, measured_at=h.measured_at, sdnn_ms=h.sdnn_ms))

    for hr in batch.heart_rate:
        db.add(
            HeartRateSample(
                user_id=user.id,
                source=src,
                measured_at=hr.measured_at,
                bpm=hr.bpm,
                context=hr.context,
            )
        )

    for bm in batch.body_metrics:
        db.add(
            BodyMetric(
                user_id=user.id,
                source=src,
                metric=bm.metric,
                value=bm.value,
                measured_at=bm.measured_at,
            )
        )

    db.commit()
    return IngestSummary(
        workouts=len(batch.workouts),
        sleep=len(batch.sleep),
        hrv=len(batch.hrv),
        heart_rate=len(batch.heart_rate),
        body_metrics=len(batch.body_metrics),
    )
