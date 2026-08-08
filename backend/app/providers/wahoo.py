"""Wahoo Cloud API provider — ELEMNT head units and KICKR trainers.

Docs:
- Portal: https://developers.wahooligan.com/cloud
- API:    https://cloud-api.wahooligan.com/

Requested by a cyclist tester, and the gap is real: an ELEMNT rider whose data
never reaches Strava has nothing here at all.

The shape of this API differs from the others in three ways worth knowing:

1. Sports are numeric ids, not strings — 0 is BIKING, 1 is RUNNING. An unmapped
   integer is meaningless in a log, so the raw id is included in the warning.
2. The interesting fields live in a nested `workout_summary` that is null until
   the ride finishes uploading. A workout without one is not a workout without
   data; it is a workout we are seeing too early, and skipping it lets the next
   sync pick it up complete.
3. Accumulator fields arrive as strings. `"3600.0"` for duration is normal, and
   passing it through unconverted would make every TRIMP calculation silently
   fall back to the duration estimate.

Wahoo caps an app at 10 unrevoked access tokens per user as of January 2026,
which affects a user reinstalling repeatedly rather than us.
"""

from __future__ import annotations

import logging
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
from app.providers.sports import Sport

log = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://api.wahooligan.com/oauth/authorize"
_TOKEN_URL = "https://api.wahooligan.com/oauth/token"
_API_BASE = "https://api.wahooligan.com/v1"

# `offline_data` is what makes the refresh token useful — without it the
# connection only works while the app is open, which for a nightly sync means it
# never works at all.
_SCOPES = ["user_read", "workouts_read", "offline_data"]

# Wahoo's numeric workout types. Only the ones we can map are listed; anything
# else becomes "other" with the id logged, so the long tail is discoverable
# rather than silently flattened.
_WAHOO_SPORT: dict[int, Sport] = {
    0: "cycling",
    1: "running",
    2: "swimming",
    3: "walking",
    5: "running",          # treadmill
    6: "cycling",
    11: "hiking",
    12: "indoor_cycling",
    13: "indoor_cycling",  # trainer
    21: "rowing",
    22: "rowing",          # indoor rower
    31: "ski_nordic",
    32: "ski_alpine",
    40: "strength",
    41: "yoga",
    61: "mtb",
}


class WahooProvider:
    info = ProviderInfo(slug="wahoo", display_name="Wahoo", auth_kind="oauth2")
    scopes = _SCOPES

    def oauth_authorize_url(self, state: str) -> str:
        cfg = settings()
        params = {
            "client_id": cfg.wahoo_client_id,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "redirect_uri": redirect_uri_for("wahoo"),
            "state": state,
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
                    "redirect_uri": redirect_uri_for("wahoo"),
                    "client_id": cfg.wahoo_client_id,
                    "client_secret": cfg.wahoo_client_secret,
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
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                    "client_id": cfg.wahoo_client_id,
                    "client_secret": cfg.wahoo_client_secret,
                },
            )
            r.raise_for_status()
            return _token_from_response(r.json(), fallback_refresh=token.refresh_token)

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
            for w in _workouts(client, since, until):
                record = _normalize(w)
                if record is not None:
                    yield record


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


def _workouts(
    client: httpx.Client, since: datetime, until: datetime
) -> Iterable[dict[str, Any]]:
    """Page newest-first, stopping once we are past the window.

    The endpoint has no date filter — it always sorts by `starts` descending —
    so the window is applied client-side and paging stops early rather than
    walking a user's entire history on every sync.
    """
    page = 1
    # 40 pages at 100 each is 4000 rides, far past any sane sync window, and a
    # backstop against a pagination bug spinning forever.
    while page <= 40:
        r = client.get(_API_BASE + "/workouts", params={"page": page, "per_page": 100})
        if r.is_error:
            log.error("wahoo: %s listing workouts — %s", r.status_code, r.text[:300])
        r.raise_for_status()
        body = r.json()
        batch = body.get("workouts", [])
        if not batch:
            return

        for w in batch:
            started = _parse_time(w.get("starts"))
            if started is None:
                continue
            if started < since:
                # Sorted descending, so everything after this is older too.
                return
            if started <= until:
                yield w

        if page * body.get("per_page", 100) >= body.get("total", 0):
            return
        page += 1


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        log.warning("wahoo: unparseable timestamp %r", value)
        return None


def _num(value: Any) -> float | None:
    """Wahoo sends accumulators as strings. Unconverted, a duration of "3600.0"
    reads as zero downstream and the session scores as if it never happened."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sport(type_id: Any) -> Sport:
    try:
        key = int(type_id)
    except (TypeError, ValueError):
        return "other"
    if key in _WAHOO_SPORT:
        return _WAHOO_SPORT[key]
    # The id is the only clue to what this was — logging "unmapped sport" alone
    # would leave nothing to act on.
    log.info("wahoo: unmapped workout_type_id %s — falling back to 'other'", key)
    return "other"


def _normalize(w: dict[str, Any]) -> NormalizedRecord | None:
    summary = w.get("workout_summary")
    if not summary:
        # Still uploading from the head unit. Skipping leaves it for the next
        # sync, when it will arrive complete — recording it now would write a
        # zero-duration workout that dedupe then prevents us from correcting.
        return None

    started = _parse_time(w.get("starts"))
    if started is None:
        return None

    duration = (
        _num(summary.get("duration_active_accum"))
        or _num(summary.get("duration_total_accum"))
        # `minutes` is on the workout itself and is the last resort.
        or (_num(w.get("minutes")) or 0) * 60
    )
    if not duration:
        return None

    avg_hr = _num(summary.get("heart_rate_avg"))
    return NormalizedRecord(
        kind="workout",
        source_record_id=str(w["id"]),
        payload={
            "started_at": started,
            "ended_at": started + timedelta(seconds=duration),
            "sport": _sport(w.get("workout_type_id")),
            "distance_m": _num(summary.get("distance_accum")),
            # Rounded to int because that is the column type; None stays None so
            # a missing strap doesn't become a heart rate of zero.
            "avg_hr": int(avg_hr) if avg_hr else None,
            "raw": w,
        },
    )
