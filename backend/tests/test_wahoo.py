"""Wahoo Cloud API provider.

Three things here differ from every other provider and are where the bugs would
live: numeric sport ids, accumulators delivered as strings, and a nested summary
that is null until the head unit finishes uploading.
"""

from datetime import datetime, timezone

import httpx
import pytest

from app.providers.base import TokenSet
from app.providers.wahoo import WahooProvider, _normalize, _num, _sport

SINCE = datetime(2026, 7, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 8, tzinfo=timezone.utc)


@pytest.fixture
def provider():
    return WahooProvider()


def _workout(**over):
    base = {
        "id": 555,
        "starts": "2026-08-06T12:00:00.000Z",
        "minutes": 60,
        "workout_type_id": 0,
        "workout_summary": {
            "duration_active_accum": "3600.0",
            "distance_accum": "40000.0",
            "heart_rate_avg": "142.5",
            "power_avg": "210.0",
        },
    }
    base.update(over)
    return base


def test_offline_scope_is_requested(provider):
    """Without offline_data there is no usable refresh token, and a nightly sync
    against a connection that only works while the app is open never runs."""
    url = provider.oauth_authorize_url("s")
    assert "offline_data" in url
    assert "workouts_read" in url
    assert "workouts_write" not in url


def test_string_accumulators_become_numbers():
    """Wahoo sends "3600.0", not 3600. Left as a string it reads as zero
    downstream and the session scores as though it never happened."""
    assert _num("3600.0") == 3600.0
    assert _num(42) == 42.0
    assert _num("") is None
    assert _num(None) is None
    assert _num("not-a-number") is None


def test_numeric_sport_ids_map_to_our_vocabulary():
    assert _sport(0) == "cycling"
    assert _sport(1) == "running"
    assert _sport(12) == "indoor_cycling"
    assert _sport("0") == "cycling", "ids sometimes arrive as strings"
    assert _sport(9999) == "other"
    assert _sport(None) == "other"


def test_normalize_extracts_the_summary():
    rec = _normalize(_workout())
    assert rec is not None
    assert rec.kind == "workout"
    assert rec.source_record_id == "555"
    p = rec.payload
    assert p["sport"] == "cycling"
    assert p["distance_m"] == 40000.0
    assert p["avg_hr"] == 142, "heart rate is what selects TRIMP over the estimate"
    # End time is derived from the active duration, not wall clock — a paused
    # ride would otherwise look an hour longer than it was.
    assert (p["ended_at"] - p["started_at"]).total_seconds() == 3600


def test_a_workout_still_uploading_is_skipped():
    """Summary is null until the ELEMNT finishes syncing. Writing it now would
    store a zero-duration ride that dedupe then stops us correcting."""
    assert _normalize(_workout(workout_summary=None)) is None


def test_zero_duration_is_skipped():
    assert _normalize(
        _workout(minutes=0, workout_summary={"duration_active_accum": "0"})
    ) is None


def test_missing_heart_rate_stays_none_not_zero():
    rec = _normalize(_workout(workout_summary={"duration_active_accum": "1800.0"}))
    assert rec.payload["avg_hr"] is None


def test_paging_stops_once_past_the_window(provider, monkeypatch):
    """The endpoint has no date filter and sorts newest-first, so paging must
    stop rather than walk the user's entire history every sync."""
    pages_fetched = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("page", 1))
        pages_fetched.append(page)
        if page == 1:
            return httpx.Response(200, json={
                "workouts": [_workout(id=1, starts="2026-08-06T12:00:00.000Z")],
                "total": 500, "page": 1, "per_page": 100,
            })
        # Older than `since` — paging must stop here.
        return httpx.Response(200, json={
            "workouts": [_workout(id=2, starts="2020-01-01T12:00:00.000Z")],
            "total": 500, "page": 2, "per_page": 100,
        })

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: real_client(**{**kw, "transport": httpx.MockTransport(handler)}),
    )

    records = list(provider.sync(TokenSet("t", "r", None), SINCE, UNTIL))
    assert [r.source_record_id for r in records] == ["1"]
    assert pages_fetched == [1, 2], "must stop at the first out-of-window page"
