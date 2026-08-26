import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)


class Workout(Base):
    __tablename__ = "workouts"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_record_id", name="uq_workout_source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_record_id: Mapped[str] = mapped_column(String(128))
    sport: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int] = mapped_column(Integer)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tss: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Every source that independently recorded this session, including the one
    # in `source` that we kept. Dedup used to delete the losers outright, which
    # threw away the most defensible thing we know about a workout: that three
    # devices saw it and we chose the record with power data.
    #
    # Provenance, not decoration. It is what makes "we reconcile across sources"
    # a checkable claim rather than a description of code nobody can see.
    contributing_sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SleepSession(Base):
    __tablename__ = "sleep_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_record_id", name="uq_sleep_source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_record_id: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deep_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    light_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rem_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awake_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HRVReading(Base):
    """HRV readings. NOTE: unit is critical — Oura is rMSSD, HealthKit is SDNN; do NOT coerce."""

    __tablename__ = "hrv_readings"
    # Natural key rather than a source_record_id column: a point measurement is
    # identified by when it was taken and who took it. One source cannot report
    # two different HRV values for the same instant.
    __table_args__ = (
        UniqueConstraint("user_id", "source", "measured_at", name="uq_hrv_measured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    source: Mapped[str] = mapped_column(String(32), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rmssd_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    sdnn_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HeartRateSample(Base):
    __tablename__ = "heart_rate_samples"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "measured_at", name="uq_hr_measured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    source: Mapped[str] = mapped_column(String(32), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bpm: Mapped[int] = mapped_column(Integer)
    context: Mapped[str | None] = mapped_column(String(16), nullable=True)  # rest|active|sleep


class ReadinessScore(Base):
    __tablename__ = "readiness_scores"
    __table_args__ = (UniqueConstraint("user_id", "source", "day", name="uq_readiness_day"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    source: Mapped[str] = mapped_column(String(32))
    day: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class BodyMetric(Base):
    __tablename__ = "body_metrics"
    # `metric` is part of the key: weight and VO2 max can share a timestamp.
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source", "metric", "measured_at", name="uq_body_metric_measured_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    source: Mapped[str] = mapped_column(String(32))
    metric: Mapped[str] = mapped_column(String(32), index=True)  # weight_kg, vo2_max, body_fat_pct
    value: Mapped[float] = mapped_column(Float)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
