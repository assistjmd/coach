"""Oura Ring API v2 provider.

Docs:
- Auth: https://cloud.ouraring.com/docs/authentication
- API:  https://cloud.ouraring.com/v2/docs

Notes:
- OAuth2 authorization-code flow with refresh tokens.
- HRV from Oura is rMSSD (ms). Do NOT coerce to SDNN.
- Rate limit: 5000 req / 5 min per token.
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
from app.providers.sports import OURA_SPORT, normalize

_AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
_TOKEN_URL = "https://api.ouraring.com/oauth/token"
_API_BASE = "https://api.ouraring.com/v2/usercollection"
_SCOPES = ["daily", "heartrate", "workout", "session", "personal", "email"]


class OuraProvider:
    info = ProviderInfo(slug="oura", display_name="Oura Ring", auth_kind="oauth2")
    scopes = _SCOPES

    # An alternative route for anyone who cannot connect directly — which, until
    # Oura approves the app, is everyone past the tenth user.
    #
    # It is genuinely worse and the copy says so. Oura does not write HRV to
    # Apple Health (nor does Whoop, nor Garmin), and its own readiness score
    # never leaves its API. Sleep, heart rate and resting heart rate do come
    # across, which is most of what the brief reads.
    workaround_title = "Connect Oura through Apple Health"
    workaround_steps = [
        "Open the Oura app and tap the menu icon.",
        "Go to Settings, then Data Sharing, then Apple Health.",
        "Turn on Connect to Health.",
        "Allow the categories Apple asks about — sleep, heart rate, "
        "resting heart rate, steps and workouts.",
        "Come back here and connect Apple Health if you have not already.",
        "One thing to know: Oura does not send HRV or its own readiness score "
        "to Apple Health. Connecting Oura directly here gives me both, and a "
        "sharper read. Apple Health still gets me your sleep and heart rate, "
        "which is most of it.",
    ]

    def oauth_authorize_url(self, state: str) -> str:
        cfg = settings()
        params = {
            "client_id": cfg.oura_client_id,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "redirect_uri": redirect_uri_for("oura"),
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
                    "redirect_uri": redirect_uri_for("oura"),
                    "client_id": cfg.oura_client_id,
                    "client_secret": cfg.oura_client_secret,
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
                    "client_id": cfg.oura_client_id,
                    "client_secret": cfg.oura_client_secret,
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
        since = since or (datetime.now(timezone.utc) - timedelta(days=30))
        until = until or datetime.now(timezone.utc)
        headers = {"Authorization": f"Bearer {token.access_token}"}
        start_d = since.date().isoformat()
        end_d = until.date().isoformat()

        with httpx.Client(timeout=30, headers=headers) as client:
            yield from _sync_sleep(client, start_d, end_d)
            yield from _sync_readiness(client, start_d, end_d)
            yield from _sync_workouts(client, start_d, end_d)
            yield from _sync_hrv(client, start_d, end_d)


def _token_from_response(data: dict[str, Any]) -> TokenSet:
    expires_in = data.get("expires_in")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if expires_in is not None
        else None
    )
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
        scope=data.get("scope"),
    )


def _paged(client: httpx.Client, path: str, params: dict[str, Any]) -> Iterable[dict[str, Any]]:
    next_token: str | None = None
    while True:
        if next_token:
            params = {**params, "next_token": next_token}
        r = client.get(f"{_API_BASE}/{path}", params=params)
        r.raise_for_status()
        body = r.json()
        for item in body.get("data", []):
            yield item
        next_token = body.get("next_token")
        if not next_token:
            return


def _sync_sleep(c: httpx.Client, start: str, end: str) -> Iterable[NormalizedRecord]:
    for s in _paged(c, "sleep", {"start_date": start, "end_date": end}):
        yield NormalizedRecord(
            kind="sleep",
            source_record_id=s["id"],
            payload={
                "started_at": s.get("bedtime_start"),
                "ended_at": s.get("bedtime_end"),
                "deep_s": s.get("deep_sleep_duration"),
                "light_s": s.get("light_sleep_duration"),
                "rem_s": s.get("rem_sleep_duration"),
                "awake_s": s.get("awake_time"),
                "efficiency": s.get("efficiency"),
                "raw": s,
            },
        )


def _sync_readiness(c: httpx.Client, start: str, end: str) -> Iterable[NormalizedRecord]:
    for r in _paged(c, "daily_readiness", {"start_date": start, "end_date": end}):
        yield NormalizedRecord(
            kind="readiness",
            source_record_id=r["id"],
            payload={"day": r.get("day"), "score": r.get("score"), "raw": r},
        )


def _sync_workouts(c: httpx.Client, start: str, end: str) -> Iterable[NormalizedRecord]:
    for w in _paged(c, "workout", {"start_date": start, "end_date": end}):
        yield NormalizedRecord(
            kind="workout",
            source_record_id=w["id"],
            payload={
                "started_at": w.get("start_datetime"),
                "ended_at": w.get("end_datetime"),
                "sport": normalize(w.get("activity"), OURA_SPORT),
                "distance_m": w.get("distance"),
                "raw": w,
            },
        )


def _sync_hrv(c: httpx.Client, start: str, end: str) -> Iterable[NormalizedRecord]:
    # HRV is delivered via sleep period samples. v2 exposes `heartrate` (HR series) and
    # sleep contains rmssd average. For MVP we pull average rmssd off each sleep record.
    for s in _paged(c, "sleep", {"start_date": start, "end_date": end}):
        rmssd = s.get("average_hrv")
        if rmssd is None:
            continue
        yield NormalizedRecord(
            kind="hrv",
            source_record_id=f"{s['id']}-rmssd",
            payload={
                "measured_at": s.get("bedtime_end"),
                "rmssd_ms": rmssd,
            },
        )
