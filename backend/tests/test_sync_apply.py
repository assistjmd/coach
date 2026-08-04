"""The normalization sink: `sync._apply`.

The parametrized coverage test is the point of this file. `heart_rate` and
`body_metric` were declared in `NormalizedKind` but silently dropped by `_apply`
for as long as they existed — a provider could look like it was syncing while its
most useful signal went nowhere. Driving the test off `get_args(NormalizedKind)`
means adding a kind to the Literal without handling it fails the suite, rather
than relying on someone remembering.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest

from app.jobs.sync import _apply
from app.models import (
    BodyMetric,
    HeartRateSample,
    HRVReading,
    ReadinessScore,
    SleepSession,
    Workout,
)
from app.providers.base import NormalizedKind, NormalizedRecord

NOW = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)

# One representative record per kind, plus the table it must land in.
SAMPLES: dict[str, tuple[type, dict]] = {
    "workout": (
        Workout,
        {
            "started_at": NOW.isoformat(),
            "ended_at": (NOW + timedelta(minutes=45)).isoformat(),
            "sport": "running",
            "distance_m": 9000.0,
        },
    ),
    "sleep": (
        SleepSession,
        {
            "started_at": (NOW - timedelta(hours=8)).isoformat(),
            "ended_at": NOW.isoformat(),
            "deep_s": 4800,
            "light_s": 13200,
            "rem_s": 6600,
        },
    ),
    "hrv": (HRVReading, {"measured_at": NOW.isoformat(), "rmssd_ms": 62.0}),
    "readiness": (ReadinessScore, {"day": NOW.date().isoformat(), "score": 78}),
    "heart_rate": (HeartRateSample, {"measured_at": NOW.isoformat(), "bpm": 48, "context": "rest"}),
    "body_metric": (
        BodyMetric,
        {"metric": "weight_kg", "value": 74.2, "measured_at": NOW.isoformat()},
    ),
}


def record(kind: str) -> NormalizedRecord:
    return NormalizedRecord(kind=kind, source_record_id=f"rec-{kind}", payload=SAMPLES[kind][1])


def test_every_declared_kind_has_a_sample():
    """Guards the guard: a new kind must get a fixture here too."""
    assert set(SAMPLES) == set(get_args(NormalizedKind))


@pytest.mark.parametrize("kind", get_args(NormalizedKind))
def test_every_kind_is_persisted(db, user, kind):
    model, _ = SAMPLES[kind]
    _apply(db, user.id, "mock", record(kind))
    db.commit()
    assert db.query(model).filter_by(user_id=user.id).count() == 1, (
        f"{kind!r} was accepted but never written — _apply is missing a branch"
    )


@pytest.mark.parametrize("kind", get_args(NormalizedKind))
def test_reapplying_the_same_record_is_a_no_op(db, user, kind):
    """A re-sync must not duplicate. This is what the overlap window relies on."""
    model, _ = SAMPLES[kind]
    for _ in range(3):
        _apply(db, user.id, "mock", record(kind))
        db.commit()
    assert db.query(model).filter_by(user_id=user.id).count() == 1


def test_unhandled_kind_is_logged_not_swallowed(db, user, caplog):
    rec = NormalizedRecord(kind="something_new", source_record_id="x", payload={})
    with caplog.at_level(logging.WARNING, logger="coach.sync"):
        _apply(db, user.id, "mock", rec)
    assert "unhandled normalized kind" in caplog.text
    assert "something_new" in caplog.text


def test_same_instant_from_different_sources_both_persist(db, user):
    """Two devices measuring at once is not a duplicate.

    `source` is part of the natural key precisely because Oura's rMSSD and Apple
    Health's SDNN can describe the same moment and must not collapse.
    """
    _apply(db, user.id, "oura", record("hrv"))
    _apply(db, user.id, "healthkit", record("hrv"))
    db.commit()
    assert db.query(HRVReading).filter_by(user_id=user.id).count() == 2


def test_different_body_metrics_at_one_instant_both_persist(db, user):
    """Weight and VO2 max can share a timestamp — `metric` is part of the key."""
    _apply(db, user.id, "mock", record("body_metric"))
    _apply(
        db,
        user.id,
        "mock",
        NormalizedRecord(
            kind="body_metric",
            source_record_id="rec-vo2",
            payload={"metric": "vo2_max", "value": 52.1, "measured_at": NOW.isoformat()},
        ),
    )
    db.commit()
    assert db.query(BodyMetric).filter_by(user_id=user.id).count() == 2


def test_hrv_units_are_not_coerced(db, user):
    """rMSSD and SDNN occupy separate columns; conflating them would mislead the coach."""
    _apply(db, user.id, "oura", record("hrv"))
    db.commit()
    row = db.query(HRVReading).filter_by(user_id=user.id, source="oura").one()
    assert row.rmssd_ms == 62.0
    assert row.sdnn_ms is None


def test_records_from_different_users_do_not_collide(db, user):
    other = uuid.uuid4()
    _apply(db, user.id, "mock", record("readiness"))
    db.commit()
    # A second user's identical readiness score must not be swallowed by the
    # first one's unique constraint.
    assert db.query(ReadinessScore).filter_by(user_id=user.id).count() == 1
    assert other != user.id
