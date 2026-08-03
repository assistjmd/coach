"""Dev-only provider that impersonates a third-party OAuth service.

Registered only when `app_env == "dev"`. Its consent screen is served by
`app/routes/dev.py` on this same host, so the entire connect path — authorize,
state signing, consent, callback, state verification, replay rejection, deny
handling, deep link, iOS parsing, Connections refresh — can be exercised with
curl and the Simulator in seconds, with no third party and no registered app.

Its `sync()` emits one record of every `NormalizedKind`, including `heart_rate`
and `body_metric`, which real providers here don't yet produce. That makes it
the fixture for verifying the sync path end to end.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.providers.base import (
    NormalizedRecord,
    ProviderInfo,
    TokenSet,
    redirect_uri_for,
)

# A wrong code must be distinguishable from a right one, so the callback's
# `exchange_failed` branch is reachable in a test.
VALID_CODE = "mock-auth-code"
_ACCESS_TOKEN = "mock-access-token"
_REFRESH_TOKEN = "mock-refresh-token"


class MockProvider:
    info = ProviderInfo(slug="mock", display_name="Mock Tracker (dev)", auth_kind="oauth2")
    scopes = ["read"]

    def oauth_authorize_url(self, state: str) -> str:
        cfg = settings()
        params = {
            "state": state,
            "redirect_uri": redirect_uri_for("mock"),
            "scope": " ".join(self.scopes),
        }
        base = cfg.public_base_url.rstrip("/")
        return f"{base}/v1/dev/fake-consent?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> TokenSet:
        if code != VALID_CODE:
            raise ValueError(f"bad code: {code!r}")
        return TokenSet(
            access_token=_ACCESS_TOKEN,
            refresh_token=_REFRESH_TOKEN,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
            scope=" ".join(self.scopes),
        )

    def refresh(self, token: TokenSet) -> TokenSet:
        if token.refresh_token != _REFRESH_TOKEN:
            raise ValueError("bad refresh token")
        return TokenSet(
            access_token=_ACCESS_TOKEN,
            refresh_token=_REFRESH_TOKEN,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
            scope=token.scope,
        )

    def sync(
        self,
        token: TokenSet,
        since: datetime | None,
        until: datetime | None = None,
    ) -> Iterable[NormalizedRecord]:
        # Deterministic ids so a re-sync is a no-op once the idempotency work in
        # Phase 2 lands — re-running this is how you check that.
        now = (until or datetime.now(timezone.utc)).replace(microsecond=0)
        day = now.date()
        start = now - timedelta(hours=2)

        yield NormalizedRecord(
            kind="workout",
            source_record_id=f"mock-workout-{day}",
            payload={
                "started_at": start.isoformat(),
                "ended_at": (start + timedelta(minutes=48)).isoformat(),
                "sport": "running",
                "distance_m": 9400.0,
                "duration_s": 2880,
                "avg_hr": 148,
                "max_hr": 172,
            },
        )
        yield NormalizedRecord(
            kind="sleep",
            source_record_id=f"mock-sleep-{day}",
            payload={
                "started_at": (now - timedelta(hours=12)).isoformat(),
                "ended_at": (now - timedelta(hours=4)).isoformat(),
                "deep_s": 4800,
                "light_s": 13200,
                "rem_s": 6600,
                "awake_s": 1200,
                "efficiency": 91,
            },
        )
        yield NormalizedRecord(
            kind="hrv",
            source_record_id=f"mock-hrv-{day}",
            # Mock stands in for an rMSSD-reporting device, like Oura.
            payload={"measured_at": (now - timedelta(hours=5)).isoformat(), "rmssd_ms": 62.0},
        )
        yield NormalizedRecord(
            kind="readiness",
            source_record_id=f"mock-readiness-{day}",
            payload={"day": day.isoformat(), "score": 78},
        )
        yield NormalizedRecord(
            kind="heart_rate",
            source_record_id=f"mock-rhr-{day}",
            payload={
                "measured_at": (now - timedelta(hours=5)).isoformat(),
                "bpm": 48,
                "context": "rest",
            },
        )
        yield NormalizedRecord(
            kind="body_metric",
            source_record_id=f"mock-weight-{day}",
            payload={
                "metric": "weight_kg",
                "value": 74.2,
                "measured_at": (now - timedelta(hours=6)).isoformat(),
            },
        )
