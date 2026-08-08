"""Google Health provider.

The API is generic — one endpoint shape for every metric — so the risks are not
in the HTTP but in the details around it: the refresh token Google only sends
once, the two spellings of a data type name, and a partial scope grant taking
down an otherwise fine sync.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.providers.base import TokenSet
from app.providers.google_health import GoogleHealthProvider, _token_from_response

SINCE = datetime(2026, 8, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 7, tzinfo=timezone.utc)


@pytest.fixture
def provider():
    return GoogleHealthProvider()


def test_authorize_url_forces_a_refresh_token(provider, monkeypatch):
    """Without access_type=offline AND prompt=consent, Google returns no refresh
    token and the connection dies an hour later."""
    url = provider.oauth_authorize_url("state-123")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=state-123" in url
    # Read-only. Asking for write we never use widens the consent screen.
    assert "writeonly" not in url
    assert "activity_and_fitness.readonly" in url


def test_refresh_carries_the_old_token_forward():
    """A refresh response omits refresh_token. Dropping it would turn a
    renewable connection into a one-hour one."""
    fresh = _token_from_response(
        {"access_token": "new", "expires_in": 3600}, fallback_refresh="original"
    )
    assert fresh.access_token == "new"
    assert fresh.refresh_token == "original"
    assert fresh.expires_at > datetime.now(timezone.utc) + timedelta(minutes=50)


def test_refresh_prefers_a_newly_issued_token():
    fresh = _token_from_response(
        {"access_token": "new", "refresh_token": "rotated"}, fallback_refresh="original"
    )
    assert fresh.refresh_token == "rotated"


def _mock(routes: dict[str, dict], seen: list | None = None):
    """Serve canned dataPoints keyed by the kebab-case type in the path."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url)
        for data_type, body in routes.items():
            if f"/dataTypes/{data_type}/" in request.url.path:
                return httpx.Response(200, json=body)
        return httpx.Response(200, json={"dataPoints": []})

    return httpx.MockTransport(handler)


def test_time_range_uses_a_filter_expression(provider, monkeypatch):
    """The bug that broke the first real connection.

    There are no startTime/endTime parameters. Google's gateway answers an
    unknown query parameter with a bare 400 that names nothing, so this is
    pinned here rather than discovered again in production.
    """
    seen: list = []
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(**{**kw, "transport": _mock({}, seen)})
    )
    list(provider.sync(TokenSet("tok", "ref", None), SINCE, UNTIL))

    by_path = {u.path.rsplit("/dataTypes/", 1)[1]: u for u in seen}

    for url in seen:
        assert "startTime" not in url.params, "startTime is not a real parameter"
        assert "endTime" not in url.params, "endTime is not a real parameter"
        assert url.params.get("filter"), "every request needs a filter expression"

    # Sessions filter on the interval; the type name is snake_case in a filter
    # even though the path is kebab-case.
    assert by_path["exercise/dataPoints"].params["filter"] == (
        'exercise.interval.start_time >= "2026-08-01T00:00:00Z" '
        'AND exercise.interval.start_time < "2026-08-07T00:00:00Z"'
    )
    # Samples filter on sample_time, not interval.
    assert by_path["heart-rate-variability/dataPoints"].params["filter"] == (
        'heart_rate_variability.sample_time.physical_time >= "2026-08-01T00:00:00Z" '
        'AND heart_rate_variability.sample_time.physical_time < "2026-08-07T00:00:00Z"'
    )
    # Dailies take a bare calendar date. Sending a timestamp is rejected.
    assert by_path["daily-resting-heart-rate/dataPoints"].params["filter"] == (
        'daily_resting_heart_rate.date >= "2026-08-01" '
        'AND daily_resting_heart_rate.date < "2026-08-07"'
    )


def test_sync_normalizes_each_kind(provider, monkeypatch):
    routes = {
        "exercise": {
            "dataPoints": [
                {
                    "name": "users/me/dataTypes/exercise/dataPoints/abc",
                    "startTime": "2026-08-06T12:00:00Z",
                    "endTime": "2026-08-06T13:00:00Z",
                    "value": {
                        "exerciseType": "RUNNING_TRAIL",
                        "distance": 10000,
                        "averageHeartRate": 152,
                        "maxHeartRate": 178,
                    },
                }
            ]
        },
        "sleep": {
            "dataPoints": [
                {
                    "name": "users/me/dataTypes/sleep/dataPoints/s1",
                    "startTime": "2026-08-05T23:00:00Z",
                    "endTime": "2026-08-06T07:00:00Z",
                    "value": {"deepSleepDuration": 3600, "efficiency": 91},
                }
            ]
        },
        "heart-rate-variability": {
            "dataPoints": [
                {
                    "name": "users/me/dataTypes/heart-rate-variability/dataPoints/h1",
                    "startTime": "2026-08-06T07:00:00Z",
                    "value": {"rmssd": 62},
                }
            ]
        },
        "daily-resting-heart-rate": {
            "dataPoints": [
                {
                    "name": "users/me/dataTypes/daily-resting-heart-rate/dataPoints/r1",
                    "startTime": "2026-08-06T07:00:00Z",
                    "value": {"bpm": 48},
                }
            ]
        },
    }

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(**{**kw, "transport": _mock(routes)})
    )

    records = list(provider.sync(TokenSet("tok", "ref", None), SINCE, UNTIL))
    by_kind = {r.kind: r for r in records}

    assert set(by_kind) == {"workout", "sleep", "hrv", "body_metric"}

    w = by_kind["workout"].payload
    assert w["sport"] == "trail_running", "Google's vocabulary must map to ours"
    assert w["avg_hr"] == 152 and w["max_hr"] == 178, "HR decides TRIMP vs fallback"
    assert w["distance_m"] == 10000

    assert by_kind["hrv"].payload["rmssd_ms"] == 62
    assert by_kind["body_metric"].payload["metric"] == "resting_hr"
    assert by_kind["body_metric"].payload["value"] == 48
    assert by_kind["sleep"].payload["efficiency"] == 91

    # Ids come from the API's own resource name, so a re-sync dedupes.
    assert by_kind["workout"].source_record_id.endswith("/abc")


def test_a_denied_scope_does_not_kill_the_whole_sync(provider, monkeypatch):
    """One missing permission must not cost us the other three data types."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/dataTypes/sleep/" in request.url.path:
            return httpx.Response(403, json={"error": "insufficient scope"})
        if "/dataTypes/exercise/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "dataPoints": [
                        {
                            "name": "x/1",
                            "startTime": "2026-08-06T12:00:00Z",
                            "endTime": "2026-08-06T13:00:00Z",
                            "value": {"exerciseType": "RUNNING"},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"dataPoints": []})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(**{**kw, "transport": httpx.MockTransport(handler)}),
    )

    records = list(provider.sync(TokenSet("tok", "ref", None), SINCE, UNTIL))
    assert [r.kind for r in records] == ["workout"]


def test_hrv_without_rmssd_is_skipped_not_guessed(provider, monkeypatch):
    """SDNN and RMSSD are different measures. Mixing them in one column makes
    the trend meaningless."""
    routes = {
        "heart-rate-variability": {
            "dataPoints": [{"name": "h/1", "startTime": "2026-08-06T07:00:00Z",
                            "value": {"sdnn": 90}}]
        }
    }
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(**{**kw, "transport": _mock(routes)})
    )
    records = list(provider.sync(TokenSet("tok", "ref", None), SINCE, UNTIL))
    assert not [r for r in records if r.kind == "hrv"]


def test_unmapped_sport_falls_back_rather_than_dropping(provider, monkeypatch):
    routes = {
        "exercise": {
            "dataPoints": [
                {
                    "name": "x/1",
                    "startTime": "2026-08-06T12:00:00Z",
                    "endTime": "2026-08-06T13:00:00Z",
                    "value": {"exerciseType": "UNDERWATER_BASKET_WEAVING"},
                }
            ]
        }
    }
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(**{**kw, "transport": _mock(routes)})
    )
    records = list(provider.sync(TokenSet("tok", "ref", None), SINCE, UNTIL))
    assert records[0].payload["sport"] == "other"
