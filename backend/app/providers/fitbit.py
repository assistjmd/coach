"""Fitbit provider.

Docs:
- Auth: https://dev.fitbit.com/build/reference/web-api/authorization/
- API:  https://dev.fitbit.com/build/reference/web-api/

Three things here differ from Oura and Strava, and each would fail in a way that
is hard to trace:

1. **Refresh tokens are single-use.** Every refresh returns a *new* refresh
   token and invalidates the old one. Persisting the response is not an
   optimisation here — miss it once and the connection is dead permanently, with
   the user seeing "please reconnect" and no explanation.
2. **The token endpoint wants HTTP Basic auth**, not client credentials in the
   form body. Sending them in the body returns a 401 that reads like a bad
   secret.
3. **HRV is rMSSD**, the same unit as Oura and *not* the SDNN that Apple Health
   reports. The system prompt has a hard rule about never comparing the two, so
   this is tagged accordingly at ingest.

Rate limit is 150 requests/hour per user, which is why the sync batches by date
range rather than fetching per-day.
"""

from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.providers.base import (
    NormalizedRecord,
    ProviderInfo,
    TokenSet,
    redirect_uri_for,
)
from app.providers.sports import FITBIT_SPORT, normalize

_AUTHORIZE_URL = "https://www.fitbit.com/oauth2/authorize"
_TOKEN_URL = "https://api.fitbit.com/oauth2/token"
_API = "https://api.fitbit.com"

_SCOPES = ["activity", "heartrate", "sleep", "profile", "weight", "cardio_fitness"]

# Fitbit caps most range endpoints at ~100 days. Kept below that so a first
# connect doesn't silently return a truncated window.
_MAX_RANGE_DAYS = 90


class FitbitProvider:
    info = ProviderInfo(slug="fitbit", display_name="Fitbit", auth_kind="oauth2")
    scopes = _SCOPES

    def oauth_authorize_url(self, state: str) -> str:
        cfg = settings()
        params = {
            "client_id": cfg.fitbit_client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri_for("fitbit"),
            "scope": " ".join(_SCOPES),
            "state": state,
        }
        return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> TokenSet:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri_for("fitbit"),
            }
        )

    def refresh(self, token: TokenSet) -> TokenSet:
        if not token.refresh_token:
            raise ValueError("no refresh token")
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": token.refresh_token}
        )

    def _token_request(self, data: dict[str, str]) -> TokenSet:
        cfg = settings()
        # Basic auth, not credentials in the body. Fitbit rejects the body form
        # with a 401 that looks like a wrong secret.
        basic = base64.b64encode(
            f"{cfg.fitbit_client_id}:{cfg.fitbit_client_secret}".encode()
        ).decode()
        with httpx.Client(timeout=15) as client:
            r = client.post(
                _TOKEN_URL,
                data=data,
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            r.raise_for_status()
            return _token_from_response(r.json())

    def sync(
        self,
        token: TokenSet,
        since: datetime | None,
        until: datetime | None = None,
    ) -> Iterable[NormalizedRecord]:
        until = until or datetime.now(timezone.utc)
        since = since or (until - timedelta(days=_MAX_RANGE_DAYS))
        # Clamp: Fitbit truncates over-long ranges without saying so, which would
        # look like missing data rather than a rejected request.
        if (until - since).days > _MAX_RANGE_DAYS:
            since = until - timedelta(days=_MAX_RANGE_DAYS)

        start, end = since.date(), until.date()
        headers = {"Authorization": f"Bearer {token.access_token}"}

        with httpx.Client(timeout=30, headers=headers) as client:
            yield from _sync_activities(client, since)
            yield from _sync_sleep(client, start, end)
            yield from _sync_hrv(client, start, end)
            yield from _sync_resting_hr(client, start, end)
            yield from _sync_vo2(client, start, end)


def _token_from_response(data: dict[str, Any]) -> TokenSet:
    expires_in = data.get("expires_in")
    return TokenSet(
        access_token=data["access_token"],
        # Always carried through: Fitbit issues a fresh refresh token on every
        # refresh and invalidates the previous one. Dropping it kills the
        # connection permanently on the next cycle.
        refresh_token=data.get("refresh_token"),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in is not None
            else None
        ),
        scope=data.get("scope"),
    )


def _get(client: httpx.Client, path: str) -> dict[str, Any]:
    r = client.get(f"{_API}{path}")
    r.raise_for_status()
    return r.json()


def _sync_activities(client: httpx.Client, since: datetime) -> Iterable[NormalizedRecord]:
    """Logged activities, newest-first paging via Fitbit's own `next` link."""
    path = (
        f"/1/user/-/activities/list.json"
        f"?afterDate={since.date().isoformat()}&sort=asc&limit=100&offset=0"
    )
    seen = 0
    while path and seen < 1000:
        body = _get(client, path)
        for a in body.get("activities", []):
            seen += 1
            started = a.get("startTime")
            duration_ms = a.get("duration") or 0
            yield NormalizedRecord(
                kind="workout",
                source_record_id=str(a.get("logId")),
                payload={
                    "started_at": started,
                    "ended_at": _end_time(started, duration_ms),
                    "sport": normalize(a.get("activityName"), FITBIT_SPORT),
                    "distance_m": _distance_m(a),
                    "duration_s": int(duration_ms / 1000) or None,
                    "avg_hr": a.get("averageHeartRate"),
                    "raw": a,
                },
            )
        nxt = (body.get("pagination") or {}).get("next")
        # The `next` link is absolute; strip the host so `_get` can prepend it.
        path = nxt.replace(_API, "") if nxt else None


def _sync_sleep(c: httpx.Client, start: date, end: date) -> Iterable[NormalizedRecord]:
    body = _get(c, f"/1.2/user/-/sleep/date/{start.isoformat()}/{end.isoformat()}.json")
    for s in body.get("sleep", []):
        levels = ((s.get("levels") or {}).get("summary")) or {}
        yield NormalizedRecord(
            kind="sleep",
            source_record_id=str(s.get("logId")),
            payload={
                "started_at": s.get("startTime"),
                "ended_at": s.get("endTime"),
                # Fitbit reports minutes; the schema stores seconds.
                "deep_s": _seconds(levels.get("deep")),
                "light_s": _seconds(levels.get("light")),
                "rem_s": _seconds(levels.get("rem")),
                "awake_s": _seconds(levels.get("wake")),
                "raw": s,
            },
        )


def _sync_hrv(c: httpx.Client, start: date, end: date) -> Iterable[NormalizedRecord]:
    """Nightly HRV.

    `dailyRmssd` is rMSSD — the same measure Oura reports, and deliberately not
    the SDNN that Apple Health provides. Mixing the two is a documented hard rule
    in the system prompt.
    """
    body = _get(c, f"/1/user/-/hrv/date/{start.isoformat()}/{end.isoformat()}.json")
    for entry in body.get("hrv", []):
        rmssd = (entry.get("value") or {}).get("dailyRmssd")
        if rmssd is None:
            continue
        day = entry.get("dateTime")
        yield NormalizedRecord(
            kind="hrv",
            source_record_id=f"fitbit-hrv-{day}",
            payload={"measured_at": f"{day}T00:00:00+00:00", "rmssd_ms": rmssd},
        )


def _sync_resting_hr(c: httpx.Client, start: date, end: date) -> Iterable[NormalizedRecord]:
    body = _get(
        c, f"/1/user/-/activities/heart/date/{start.isoformat()}/{end.isoformat()}.json"
    )
    for entry in body.get("activities-heart", []):
        resting = (entry.get("value") or {}).get("restingHeartRate")
        if resting is None:
            continue
        day = entry.get("dateTime")
        yield NormalizedRecord(
            kind="body_metric",
            source_record_id=f"fitbit-rhr-{day}",
            payload={
                "metric": "resting_hr",
                "value": resting,
                "measured_at": f"{day}T00:00:00+00:00",
            },
        )


def _sync_vo2(c: httpx.Client, start: date, end: date) -> Iterable[NormalizedRecord]:
    body = _get(c, f"/1/user/-/cardioscore/date/{start.isoformat()}/{end.isoformat()}.json")
    for entry in body.get("cardioScore", []):
        raw = (entry.get("value") or {}).get("vo2Max")
        value = _vo2_value(raw)
        if value is None:
            continue
        day = entry.get("dateTime")
        yield NormalizedRecord(
            kind="body_metric",
            source_record_id=f"fitbit-vo2-{day}",
            payload={
                "metric": "vo2_max",
                "value": value,
                "measured_at": f"{day}T00:00:00+00:00",
            },
        )


def _vo2_value(raw: Any) -> float | None:
    """Fitbit returns VO2 max either as a number or as a range string ("42-46").

    The range form appears for users without a recent qualifying run. Taking the
    midpoint keeps a usable trend instead of dropping those days entirely.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw)
    if "-" in text:
        try:
            low, high = (float(p) for p in text.split("-", 1))
            return (low + high) / 2
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _seconds(minutes: Any) -> int | None:
    return int(minutes) * 60 if isinstance(minutes, (int, float)) else None


def _distance_m(activity: dict[str, Any]) -> float | None:
    """Distance comes back in the account's own units, so the unit field decides."""
    distance = activity.get("distance")
    if distance is None:
        return None
    unit = (activity.get("distanceUnit") or "").lower()
    factor = {"kilometer": 1000, "km": 1000, "mile": 1609.344, "meter": 1}.get(unit, 1000)
    return float(distance) * factor


def _end_time(start: str | None, duration_ms: int) -> str | None:
    if not start:
        return None
    try:
        started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (started + timedelta(milliseconds=duration_ms)).isoformat()
