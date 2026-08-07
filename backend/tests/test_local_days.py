"""Calendar days belong to the athlete, not to the server clock.

Every case here is the same failure: a session logged in the evening in a western
timezone is already tomorrow in UTC, so anything that groups on the raw timestamp
files it against the wrong day. The visible symptom is a calendar that shows
yesterday's workout as today's.
"""

from datetime import date, datetime, timezone

import pytest

from app.analytics.load import daily_loads
from app.dates import local_day, today_for, zone_for
from app.models import User, Workout


class _FakeUser:
    def __init__(self, tz):
        self.id = "u"
        self.timezone = tz


def test_evening_workout_stays_on_the_day_it_was_done():
    """8pm Boston on the 6th is 00:xx UTC on the 7th."""
    moment = datetime(2026, 8, 7, 0, 30, tzinfo=timezone.utc)
    assert local_day(moment, zone_for(_FakeUser("America/New_York"))) == date(2026, 8, 6)
    assert local_day(moment, zone_for(_FakeUser("UTC"))) == date(2026, 8, 7)


def test_naive_timestamps_are_read_as_utc():
    """What the database stores. Reading them as local would shift all history."""
    naive = datetime(2026, 8, 7, 0, 30)
    assert local_day(naive, zone_for(_FakeUser("America/New_York"))) == date(2026, 8, 6)


def test_morning_workout_east_of_utc_moves_forward():
    """The bug is symmetric — Tokyo crosses the boundary the other way."""
    moment = datetime(2026, 8, 6, 22, 0, tzinfo=timezone.utc)
    assert local_day(moment, zone_for(_FakeUser("Asia/Tokyo"))) == date(2026, 8, 7)


@pytest.mark.parametrize("bad", ["", None, "Mars/Olympus_Mons", "EST5EDT-nonsense"])
def test_unusable_timezone_falls_back_rather_than_raising(bad):
    """The value comes from the device. A bad one must not 500 the calendar."""
    assert today_for(_FakeUser(bad)) == datetime.now(tz=timezone.utc).date()


def test_daily_loads_buckets_by_local_day(db):
    """The end-to-end version: the load must land on the athlete's day.

    Also covers the widened SQL window — a workout inside the local range but
    outside it in UTC would otherwise be filtered out before Python ever sees it.
    """
    user = User(email="tz@example.com", timezone="America/New_York")
    db.add(user)
    db.flush()

    db.add(
        Workout(
            user_id=user.id,
            source="strava",
            source_record_id="evening-1",
            # 8:30pm on the 6th in Boston; the 7th in UTC.
            started_at=datetime(2026, 8, 7, 0, 30, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc),
            duration_s=3600,
            sport="running",
        )
    )
    db.flush()

    loads = daily_loads(db, user.id, date(2026, 8, 6), date(2026, 8, 6))
    assert date(2026, 8, 6) in loads, "evening session filed under the wrong day"
    assert loads[date(2026, 8, 6)][0] > 0
