"""Fitbit's three sharp edges.

Each fails in a way that is hard to trace back: a dropped refresh token kills
the connection days later, credentials in the wrong place look like a bad
secret, and a mis-parsed unit produces plausible-but-wrong distances that nobody
notices.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.providers.fitbit import (
    _distance_m,
    _end_time,
    _seconds,
    _token_from_response,
    _vo2_value,
)
from app.providers.sports import FITBIT_SPORT, normalize


def test_refresh_token_is_carried_through():
    """Fitbit invalidates the old refresh token on every refresh. Dropping the
    new one leaves the connection permanently dead on the next cycle."""
    ts = _token_from_response(
        {"access_token": "a", "refresh_token": "rotated", "expires_in": 28800}
    )
    assert ts.refresh_token == "rotated"


def test_expiry_is_relative_not_absolute():
    """Fitbit sends expires_in (seconds), where Strava sends expires_at (epoch).
    Reading one as the other puts expiry in 1970 or the far future."""
    before = datetime.now(timezone.utc)
    ts = _token_from_response({"access_token": "a", "expires_in": 28800})
    assert ts.expires_at is not None
    delta = ts.expires_at - before
    assert timedelta(hours=7) < delta < timedelta(hours=9)


def test_missing_expiry_is_none_not_zero():
    ts = _token_from_response({"access_token": "a"})
    assert ts.expires_at is None


# --- units --------------------------------------------------------------


@pytest.mark.parametrize(
    "unit,value,expected",
    [
        ("Kilometer", 5, 5000),
        ("km", 5, 5000),
        ("Mile", 1, 1609.344),
        ("Meter", 400, 400),
    ],
)
def test_distance_respects_the_account_unit(unit, value, expected):
    """Distance arrives in the user's own units. Assuming km would report a
    5-mile run as 5km — wrong, but not obviously wrong."""
    got = _distance_m({"distance": value, "distanceUnit": unit})
    assert got == pytest.approx(expected)


def test_missing_distance_is_none():
    assert _distance_m({"activityName": "Weights"}) is None


def test_sleep_minutes_become_seconds():
    assert _seconds(90) == 5400
    assert _seconds(None) is None


def test_vo2_max_range_string_is_averaged():
    """Fitbit returns a range like "42-46" for users without a recent qualifying
    run. Dropping those days would leave the trend full of holes."""
    assert _vo2_value("42-46") == 44
    assert _vo2_value(45.2) == 45.2
    assert _vo2_value(None) is None
    assert _vo2_value("nonsense") is None


def test_end_time_derives_from_duration():
    end = _end_time("2026-08-06T06:00:00.000+00:00", 45 * 60 * 1000)
    assert end is not None and "06:45" in end


def test_malformed_start_does_not_raise():
    assert _end_time("not-a-date", 1000) is None
    assert _end_time(None, 1000) is None


# --- sports -------------------------------------------------------------


def test_known_activities_map_to_the_shared_vocabulary():
    assert normalize("Run", FITBIT_SPORT) == "running"
    assert normalize("Spinning", FITBIT_SPORT) == "indoor_cycling"
    assert normalize("Weights", FITBIT_SPORT) == "strength"


def test_unknown_activity_falls_back_rather_than_leaking_through():
    """An unmapped name must not reach the workouts table as a fourth
    vocabulary — that is what the shared mapping exists to prevent."""
    assert normalize("Underwater Basket Weaving", FITBIT_SPORT) == "other"
