"""Google Health API provider — Fitbit, Pixel Watch, and Health Connect data.

Docs:
- Setup:      https://developers.google.com/health/setup
- REST:       https://developers.google.com/health/reference/rest
- Data types: https://developers.google.com/health/data-types

This is the successor to the Fitbit Web API, which shuts down in September 2026.
Building here rather than against Fitbit directly was the whole point: a Fitbit
integration written today would need replacing within weeks, and existing Fitbit
OAuth tokens do not carry over regardless.

Three things about this API that the code below is shaped by:

1. There are no per-metric endpoints. Everything goes through one generic
   `users/{user}/dataTypes/{type}/dataPoints` collection, so each metric is the
   same call with a different type name.
2. Type names are kebab-case in a path and snake_case in a filter. Same data
   type, two spellings, and getting it wrong is a 404 rather than an error that
   explains itself.
3. Access is capped at 100 users until a third-party security review is
   completed. That is ten times Strava's and Oura's ceiling, so this is not the
   binding constraint on the beta.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.providers.base import (
    NormalizedRecord,
    ProviderInfo,
    ReauthRequired,
    TokenSet,
    redirect_uri_for,
)
from app.providers.sports import Sport, normalize

log = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://health.googleapis.com/v4"

# Requested read-only. There is no write path in this app and asking for one
# would widen the consent screen for capability we never use — on a health app
# that is the difference between a user granting access and backing out.
_SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]

# Google's exercise-type vocabulary. Unmapped values fall through to "other" and
# are logged, so the long tail surfaces instead of silently flattening.
_GOOGLE_SPORT: dict[str, Sport] = {
    "RUNNING": "running",
    "RUNNING_TREADMILL": "running",
    "RUNNING_TRAIL": "trail_running",
    "BIKING": "cycling",
    "BIKING_STATIONARY": "indoor_cycling",
    "BIKING_MOUNTAIN": "mtb",
    "SWIMMING_POOL": "swimming",
    "SWIMMING_OPEN_WATER": "swimming",
    "ROWING": "rowing",
    "ROWING_MACHINE": "rowing",
    "STRENGTH_TRAINING": "strength",
    "WEIGHTLIFTING": "strength",
    "WALKING": "walking",
    "HIKING": "hiking",
    "YOGA": "yoga",
    "SKIING_CROSS_COUNTRY": "ski_nordic",
    "SKIING": "ski_alpine",
    "SNOWBOARDING": "ski_alpine",
}


class GoogleHealthProvider:
    info = ProviderInfo(
        slug="google_health", display_name="Google Health", auth_kind="oauth2"
    )
    scopes = _SCOPES

    def oauth_authorize_url(self, state: str) -> str:
        cfg = settings()
        params = {
            "client_id": cfg.google_health_client_id,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "redirect_uri": redirect_uri_for("google_health"),
            "state": state,
            # Google only returns a refresh token when both are set, and only on
            # the FIRST consent. Without "consent" a returning user who
            # reconnects gets an access token that expires in an hour and no way
            # to renew it — the connection then dies silently a day later.
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> TokenSet:
        cfg = settings()
        with httpx.Client(timeout=15) as client:
            r = client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri_for("google_health"),
                    "client_id": cfg.google_health_client_id,
                    "client_secret": cfg.google_health_client_secret,
                },
            )
            _check_token_response(r)
            return _token_from_response(r.json())

    def refresh(self, token: TokenSet) -> TokenSet:
        cfg = settings()
        if not token.refresh_token:
            raise ValueError("no refresh token")
        with httpx.Client(timeout=15) as client:
            r = client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                    "client_id": cfg.google_health_client_id,
                    "client_secret": cfg.google_health_client_secret,
                },
            )
            _check_token_response(r)
            data = r.json()
            # A refresh response omits the refresh token. Carrying the old one
            # forward is required — dropping it turns a renewable connection
            # into a one-hour one.
            return _token_from_response(data, fallback_refresh=token.refresh_token)

    def sync(
        self,
        token: TokenSet,
        since: datetime | None,
        until: datetime | None = None,
    ) -> Iterable[NormalizedRecord]:
        since = since or (datetime.now(timezone.utc) - timedelta(days=30))
        until = until or datetime.now(timezone.utc)
        headers = {"Authorization": f"Bearer {token.access_token}"}

        with httpx.Client(timeout=30, headers=headers) as client:
            yield from _sync_exercise(client, since, until)
            yield from _sync_sleep(client, since, until)
            yield from _sync_hrv(client, since, until)
            yield from _sync_resting_hr(client, since, until)


def _token_from_response(
    data: dict[str, Any], fallback_refresh: str | None = None
) -> TokenSet:
    expires_in = data.get("expires_in")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if expires_in is not None
        else None
    )
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or fallback_refresh,
        expires_at=expires_at,
        scope=data.get("scope"),
    )


def _check_token_response(r: httpx.Response) -> None:
    """Raise something meaningful when the token endpoint refuses.

    `invalid_grant` here is nearly always Google's seven-day revocation for
    OAuth apps whose publishing status is still Testing. It is not recoverable
    by retrying, and the athlete cannot act on `HTTPStatusError: 400 Bad
    Request` — which is all this used to produce, because the body was never
    logged on the token endpoint the way it is on the data calls.
    """
    if not r.is_error:
        return
    body = r.text[:400]
    log.error("google_health: %s from token endpoint — %s", r.status_code, body)
    try:
        err = r.json().get("error")
    except ValueError:
        err = None
    if err in ("invalid_grant", "unauthorized_client"):
        raise ReauthRequired(
            "Google Health needs reconnecting. Tap Connect again on Sources."
        )
    r.raise_for_status()


class AccountNotLinked(RuntimeError):
    """The Google account has never been set up with Google Health.

    Granting OAuth consent and *having a Google Health account* are two separate
    things, and nothing on the consent screen says so. Every Google user can
    authorize us; only those who have completed Fitbit/Google Health signup have
    anything behind it.

    Raised as its own type so the message reaching the user is an instruction
    rather than `HTTPStatusError: Client error '400 Bad Request' for url ...`.
    """


def _raise_if_unlinked(r: httpx.Response) -> None:
    """Turn Google's FAILED_PRECONDITION into something a person can act on."""
    try:
        err = r.json().get("error", {})
    except ValueError:
        return
    reasons = {
        d.get("reason") for d in err.get("details", []) if isinstance(d, dict)
    }
    if err.get("status") == "FAILED_PRECONDITION" or "ACCOUNT_NOT_LINKED" in reasons:
        raise AccountNotLinked(
            "This Google account isn't set up with Google Health yet. "
            "Finish signup at fitbit.google.com, then reconnect."
        )


@dataclass(frozen=True)
class _Type:
    """How to address one data type.

    The two spellings are not interchangeable and there is no way to derive one
    call from the other, so both are written out:

    `path` is kebab-case and goes in the URL. `field` is the snake_case name
    plus the time field for that record's shape — Sessions and Intervals are
    filtered on `interval.start_time`, Samples on `sample_time.physical_time`,
    and Dailies on a bare `date`. Filtering a Sample as though it were a Session
    is a 400 with no explanation of which half was wrong.
    """

    path: str
    field: str
    # Dailies take a calendar date, not a timestamp. Sending RFC-3339 to one is
    # rejected.
    date_only: bool = False


_EXERCISE = _Type("exercise", "exercise.interval.start_time")
_SLEEP = _Type("sleep", "sleep.interval.start_time")
_HRV = _Type(
    "heart-rate-variability",
    "heart_rate_variability.sample_time.physical_time",
)
_RESTING_HR = _Type("daily-resting-heart-rate", "daily_resting_heart_rate.date", date_only=True)


def _points(
    client: httpx.Client,
    spec: _Type,
    since: datetime,
    until: datetime,
) -> Iterable[dict[str, Any]]:
    """Page through one data type's points.

    Time ranges go in a single AIP-160 `filter` expression. There are no
    `startTime`/`endTime` parameters, and Google's gateway answers an unknown
    query parameter with a bare 400 that names nothing — which is why the
    response body is logged below rather than left to `raise_for_status`.
    """
    if spec.date_only:
        lo, hi = since.date().isoformat(), until.date().isoformat()
    else:
        lo = since.isoformat().replace("+00:00", "Z")
        hi = until.isoformat().replace("+00:00", "Z")
    # Half-open, matching the operators the API supports: >= and <.
    filter_expr = f'{spec.field} >= "{lo}" AND {spec.field} < "{hi}"'

    page_token: str | None = None
    # A runaway loop on a malformed cursor would hammer the API. 50 pages at
    # 1000 each is far past any real 30-day window.
    for _ in range(50):
        params: dict[str, Any] = {"filter": filter_expr, "pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        r = client.get(
            f"{_API_BASE}/users/me/dataTypes/{spec.path}/dataPoints", params=params
        )
        if r.status_code == 403:
            # Scope not granted. One missing permission must not abort the whole
            # sync — the other three data types are still worth having.
            log.warning("google_health: no access to %s", spec.path)
            return
        if r.is_error:
            # The body carries the actual reason; the status alone does not.
            log.error(
                "google_health: %s on %s — %s", r.status_code, spec.path, r.text[:500]
            )
            _raise_if_unlinked(r)
        r.raise_for_status()
        body = r.json()
        yield from body.get("dataPoints", [])
        page_token = body.get("nextPageToken")
        if not page_token:
            return
    log.warning("google_health: page cap hit for %s", spec.path)


def _pid(point: dict[str, Any], suffix: str = "") -> str:
    """A stable id for deduplication.

    `name` is the API's own resource identifier and is what we want. Falling
    back to the time window keeps ingestion working if it is ever absent, at the
    cost of a duplicate if the same window is re-reported.
    """
    base = point.get("name") or f"{point.get('startTime')}-{point.get('endTime')}"
    return f"{base}{suffix}"


def _sync_exercise(
    c: httpx.Client, since: datetime, until: datetime
) -> Iterable[NormalizedRecord]:
    for p in _points(c, _EXERCISE, since, until):
        v = p.get("value") or {}
        yield NormalizedRecord(
            kind="workout",
            source_record_id=_pid(p),
            payload={
                "started_at": p.get("startTime"),
                "ended_at": p.get("endTime"),
                "sport": normalize(v.get("exerciseType"), _GOOGLE_SPORT),
                "distance_m": v.get("distance"),
                "avg_hr": v.get("averageHeartRate"),
                "max_hr": v.get("maxHeartRate"),
                "raw": p,
            },
        )


def _sync_sleep(
    c: httpx.Client, since: datetime, until: datetime
) -> Iterable[NormalizedRecord]:
    for p in _points(c, _SLEEP, since, until):
        v = p.get("value") or {}
        yield NormalizedRecord(
            kind="sleep",
            source_record_id=_pid(p),
            payload={
                "started_at": p.get("startTime"),
                "ended_at": p.get("endTime"),
                "deep_s": v.get("deepSleepDuration"),
                "light_s": v.get("lightSleepDuration"),
                "rem_s": v.get("remSleepDuration"),
                "awake_s": v.get("awakeDuration"),
                "efficiency": v.get("efficiency"),
                "raw": p,
            },
        )


def _sync_hrv(
    c: httpx.Client, since: datetime, until: datetime
) -> Iterable[NormalizedRecord]:
    for p in _points(c, _HRV, since, until):
        v = p.get("value") or {}
        # Google reports HRV as RMSSD, same measure as Oura. Storing an SDNN
        # value in this column alongside RMSSD would make the trend meaningless,
        # so anything without an explicit rmssd is skipped rather than guessed.
        rmssd = v.get("rmssd") or v.get("heartRateVariabilityMillis")
        if rmssd is None:
            continue
        yield NormalizedRecord(
            kind="hrv",
            source_record_id=_pid(p, "-rmssd"),
            payload={"measured_at": p.get("startTime"), "rmssd_ms": rmssd},
        )


def _sync_resting_hr(
    c: httpx.Client, since: datetime, until: datetime
) -> Iterable[NormalizedRecord]:
    for p in _points(c, _RESTING_HR, since, until):
        v = p.get("value") or {}
        bpm = v.get("bpm") or v.get("beatsPerMinute")
        if bpm is None:
            continue
        yield NormalizedRecord(
            kind="body_metric",
            source_record_id=_pid(p, "-rhr"),
            payload={
                "measured_at": p.get("startTime"),
                "metric": "resting_hr",
                "value": bpm,
            },
        )
