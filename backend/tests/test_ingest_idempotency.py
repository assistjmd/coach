"""HealthKit ingest must survive being replayed.

Anchored queries plus observer firings mean the app re-sends overlapping windows
routinely — that is normal operation, not an error. Before the unique
constraints existed, every replay duplicated every HRV, heart-rate, and body
metric row, quietly inflating the data the coach reasons over.
"""

from datetime import datetime, timedelta, timezone

from app.models import BodyMetric, HeartRateSample, HRVReading, SleepSession, Workout

NOW = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)

TABLES = [Workout, SleepSession, HRVReading, HeartRateSample, BodyMetric]


def batch() -> dict:
    return {
        "workouts": [
            {
                "source_record_id": "hk-workout-1",
                "sport": "running",
                "started_at": NOW.isoformat(),
                "ended_at": (NOW + timedelta(minutes=45)).isoformat(),
                "duration_s": 2700,
                "distance_m": 9000.0,
                "avg_hr": 148,
                "max_hr": 172,
            }
        ],
        "sleep": [
            {
                "source_record_id": "hk-sleep-1",
                "started_at": (NOW - timedelta(hours=8)).isoformat(),
                "ended_at": NOW.isoformat(),
                "deep_s": 4800,
                "light_s": 13200,
                "rem_s": 6600,
                "awake_s": 1200,
            }
        ],
        "hrv": [{"measured_at": NOW.isoformat(), "sdnn_ms": 58.0}],
        "heart_rate": [{"measured_at": NOW.isoformat(), "bpm": 48, "context": "rest"}],
        "body_metrics": [
            {"metric": "weight_kg", "value": 74.2, "measured_at": NOW.isoformat()}
        ],
    }


def counts(db, user) -> dict[str, int]:
    return {t.__tablename__: db.query(t).filter_by(user_id=user.id).count() for t in TABLES}


def test_ingest_requires_auth(client):
    assert client.post("/v1/ingest/healthkit", json=batch()).status_code == 401


def test_replaying_a_batch_writes_nothing_new(client, db, user, auth):
    first = client.post("/v1/ingest/healthkit", json=batch(), headers=auth)
    assert first.status_code == 200, first.text
    after_first = counts(db, user)
    assert after_first == {t.__tablename__: 1 for t in TABLES}

    for _ in range(3):
        again = client.post("/v1/ingest/healthkit", json=batch(), headers=auth)
        assert again.status_code == 200
    assert counts(db, user) == after_first


def test_summary_reports_rows_written_not_submitted(client, db, user, auth):
    """A replayed batch should say it wrote nothing, not claim a full ingest."""
    first = client.post("/v1/ingest/healthkit", json=batch(), headers=auth).json()
    assert first == {"workouts": 1, "sleep": 1, "hrv": 1, "heart_rate": 1, "body_metrics": 1}

    second = client.post("/v1/ingest/healthkit", json=batch(), headers=auth).json()
    assert second == {"workouts": 0, "sleep": 0, "hrv": 0, "heart_rate": 0, "body_metrics": 0}


def test_duplicates_within_one_batch_are_collapsed(client, db, user, auth):
    """The constraint can't catch these — they arrive in the same statement."""
    b = batch()
    b["hrv"] = [{"measured_at": NOW.isoformat(), "sdnn_ms": 58.0}] * 4
    b["heart_rate"] = [{"measured_at": NOW.isoformat(), "bpm": 48, "context": "rest"}] * 4
    r = client.post("/v1/ingest/healthkit", json=b, headers=auth)
    assert r.status_code == 200, r.text
    assert db.query(HRVReading).filter_by(user_id=user.id).count() == 1
    assert db.query(HeartRateSample).filter_by(user_id=user.id).count() == 1


def test_an_empty_batch_is_accepted(client, user, auth):
    empty = {"workouts": [], "sleep": [], "hrv": [], "heart_rate": [], "body_metrics": []}
    r = client.post("/v1/ingest/healthkit", json=empty, headers=auth)
    assert r.status_code == 200
    assert sum(r.json().values()) == 0


def test_new_samples_still_land_after_a_replay(client, db, user, auth):
    """Idempotency must not turn into 'ignores everything after the first batch'."""
    client.post("/v1/ingest/healthkit", json=batch(), headers=auth)
    later = batch()
    later["hrv"] = [{"measured_at": (NOW + timedelta(hours=1)).isoformat(), "sdnn_ms": 61.0}]
    r = client.post("/v1/ingest/healthkit", json=later, headers=auth)
    assert r.json()["hrv"] == 1
    assert db.query(HRVReading).filter_by(user_id=user.id).count() == 2
