"""Strava provider.

Docs:
- Auth: https://developers.strava.com/docs/authentication/
- API:  https://developers.strava.com/docs/reference/

Notes worth knowing before changing anything here:

- Strava access tokens last six hours. Unlike Oura, where a stale token is a rare
  event, refresh is the normal path — anything that syncs daily will refresh
  almost every time.
- Rate limits are 100 requests per 15 minutes and 1,000 per day, per application
  and shared across all users. Paging is therefore capped rather than unbounded:
  one athlete with a decade of rides must not spend the whole app's budget.
- `sport_type` is the current field. `type` is deprecated and collapses distinct
  activities (it reports a gravel ride as "Ride"), so the mapping in sports.py
  keys off `sport_type`.
- Strava has no sleep or HRV. It contributes workouts only, which is the point:
  it is the training-load source, while Oura and HealthKit cover recovery.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.providers.base import (
    NormalizedRecord,
    ProviderInfo,
    TokenSet,
    redirect_uri_for,
)
from app.providers.sports import STRAVA_SPORT, normalize

_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
_TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"
_API_BASE = "https://www.strava.com/api/v3"

# `activity:read_all` covers private activities too; without it a user's private
# rides are silently missing and the coach under-counts their training load.
_SCOPES = ["read", "activity:read_all", "profile:read_all"]

_PER_PAGE = 100
# Ceiling on pages per sync. At 100 activities a page this is 2,000 activities,
# far more than any incremental sync needs, but it bounds a first connect for an
# athlete with a long history so one user cannot exhaust the shared rate limit.
_MAX_PAGES = 20


class StravaProvider:
    # See the note on OuraProvider. Strava's own cap is ten connected athletes
    # until they review the app, so this is the route most people will need.
    #
    # The loss here is heart rate: Strava sends activity type, distance, time
    # and calories to Apple Health, not HR. Without it a session is scored by
    # the duration estimate rather than TRIMP. Anyone recording on an Apple
    # Watch is unaffected — the Watch writes its own record with heart rate, and
    # we keep whichever copy carries more signal.
    workaround_title = "Connect Strava through Apple Health"
    workaround_steps = [
        "Open Strava and go to the You tab, then tap the settings icon.",
        "Tap Manage Apps and Devices, then Health, then the plus icon.",
        "Tap Connect, then enable Workouts & Routes.",
        "Turn on Send to Health.",
        "Come back here and connect Apple Health if you have not already.",
        "One thing to know: Strava does not send heart rate to Apple Health, so "
        "I score those sessions by duration rather than effort. If you record "
        "on an Apple Watch I get the heart rate anyway. Connecting Strava "
        "directly here is still the better read.",
    ]

    info = ProviderInfo(slug="strava", display_name="Strava", auth_kind="oauth2")
    scopes = _SCOPES

    def oauth_authorize_url(self, state: str) -> str:
        cfg = settings()
        params = {
            "client_id": cfg.strava_client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri_for("strava"),
            "approval_prompt": "auto",
            # Strava wants scopes comma-separated. Space-separated (the OAuth2
            # norm, and what Oura uses) is silently treated as a single unknown
            # scope and the grant comes back without activity access.
            "scope": ",".join(_SCOPES),
            "state": state,
        }
        return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> TokenSet:
        cfg = settings()
        with httpx.Client(timeout=15) as client:
            r = client.post(
                _TOKEN_URL,
                data={
                    "client_id": cfg.strava_client_id,
                    "client_secret": cfg.strava_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            r.raise_for_status()
            return _token_from_response(r.json())

    def refresh(self, token: TokenSet) -> TokenSet:
        cfg = settings()
        if not token.refresh_token:
            raise ValueError("no refresh token")
        with httpx.Client(timeout=15) as client:
            r = client.post(
                _TOKEN_URL,
                data={
                    "client_id": cfg.strava_client_id,
                    "client_secret": cfg.strava_client_secret,
                    "refresh_token": token.refresh_token,
                    "grant_type": "refresh_token",
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
        since = since or (datetime.now(timezone.utc) - timedelta(days=90))
        until = until or datetime.now(timezone.utc)
        headers = {"Authorization": f"Bearer {token.access_token}"}

        with httpx.Client(timeout=30, headers=headers) as client:
            for activity in _activities(client, since, until):
                yield _as_workout(activity)


def _token_from_response(data: dict[str, Any]) -> TokenSet:
    """Build a TokenSet from Strava's response.

    Strava returns `expires_at` as an absolute unix timestamp, where Oura returns
    a relative `expires_in`. Reading one as the other yields an expiry in 1970 or
    in the far future, so this handles the absolute form and only falls back to
    the relative one.
    """
    expires_at: datetime | None = None
    if (absolute := data.get("expires_at")) is not None:
        expires_at = datetime.fromtimestamp(int(absolute), tz=timezone.utc)
    elif (relative := data.get("expires_in")) is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(relative))

    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
        scope=data.get("scope"),
    )


def _activities(
    client: httpx.Client, since: datetime, until: datetime
) -> Iterable[dict[str, Any]]:
    """Page through athlete activities in the window.

    `after` and `before` are unix seconds and are exclusive bounds.
    """
    params_base = {
        "after": int(since.timestamp()),
        "before": int(until.timestamp()),
        "per_page": _PER_PAGE,
    }
    for page in range(1, _MAX_PAGES + 1):
        r = client.get(f"{_API_BASE}/athlete/activities", params={**params_base, "page": page})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            return
        yield from batch
        # A short page means there is nothing after it; asking for the next one
        # would spend a request against the shared rate limit for no reason.
        if len(batch) < _PER_PAGE:
            return


def _as_workout(a: dict[str, Any]) -> NormalizedRecord:
    started = a.get("start_date")
    ended = _end_time(started, a.get("elapsed_time"))
    return NormalizedRecord(
        kind="workout",
        # Activity ids are ints; the column is text and every other provider
        # sends a string.
        source_record_id=str(a["id"]),
        payload={
            "started_at": started,
            "ended_at": ended,
            "sport": normalize(a.get("sport_type") or a.get("type"), STRAVA_SPORT),
            "distance_m": a.get("distance"),
            # `moving_time` rather than `elapsed_time`: elapsed includes coffee
            # stops and traffic lights, which would inflate training load.
            "duration_s": a.get("moving_time"),
            "avg_hr": _as_int(a.get("average_heartrate")),
            "max_hr": _as_int(a.get("max_heartrate")),
            "raw": a,
        },
    )


def _end_time(start: str | None, elapsed_s: int | None) -> str | None:
    """Strava gives a start and a duration; the schema wants an end."""
    if not start or elapsed_s is None:
        return None
    try:
        started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (started + timedelta(seconds=int(elapsed_s))).isoformat()


def _as_int(value: Any) -> int | None:
    """Heart rates arrive as floats (e.g. 148.6) but the column is an int."""
    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None
