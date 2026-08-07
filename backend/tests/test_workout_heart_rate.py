"""Heart rate has to survive the trip from provider payload to database row.

It was not surviving. Strava emitted `avg_hr` and `max_hr`, `_apply` never wrote
them, and so every cloud workout was scored by the duration fallback rather than
TRIMP — the exponential that makes the whole load metric worth having.

The gap was invisible because both paths produce a plausible number.
"""

from datetime import datetime, timezone

from app.jobs.sync import _apply
from app.models import User, Workout
from app.providers.base import NormalizedRecord


def _record(**payload) -> NormalizedRecord:
    base = {
        "started_at": datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 8, 6, 13, 0, tzinfo=timezone.utc),
        "sport": "running",
    }
    return NormalizedRecord(
        kind="workout", source_record_id="hr-1", payload={**base, **payload}
    )


def test_heart_rate_is_persisted(db):
    user = User(email="hr@example.com", timezone="America/New_York")
    db.add(user)
    db.flush()

    _apply(db, user.id, "strava", _record(avg_hr=152, max_hr=178))
    db.flush()

    w = db.query(Workout).filter(Workout.user_id == user.id).one()
    assert w.avg_hr == 152
    assert w.max_hr == 178


def test_absent_heart_rate_stays_absent(db):
    """A hand-logged lift has none, and inventing one would be worse than the
    honest duration estimate."""
    user = User(email="nohr@example.com", timezone="America/New_York")
    db.add(user)
    db.flush()

    _apply(db, user.id, "strava", _record())
    db.flush()

    w = db.query(Workout).filter(Workout.user_id == user.id).one()
    assert w.avg_hr is None
    assert w.max_hr is None


def test_persisted_heart_rate_selects_trimp(db):
    """The reason the field matters: it changes which method scores the day."""
    from app.analytics.load import daily_loads

    user = User(email="trimp@example.com", timezone="UTC")
    db.add(user)
    db.flush()

    _apply(db, user.id, "strava", _record(avg_hr=152, max_hr=178))
    db.flush()

    day = datetime(2026, 8, 6, tzinfo=timezone.utc).date()
    _, method = daily_loads(db, user.id, day, day)[day]
    assert method == "trimp"
