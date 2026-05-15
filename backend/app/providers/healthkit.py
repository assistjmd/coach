"""HealthKit "virtual" provider — does not OAuth.

iOS reads from HealthKit on-device and POSTs normalized batches to
`/v1/ingest/healthkit`. This module exists so the registry treats HealthKit
uniformly with cloud providers (display in UI, last-synced timestamp, etc.).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from app.providers.base import NormalizedRecord, ProviderInfo, TokenSet


class HealthKitProvider:
    info = ProviderInfo(slug="healthkit", display_name="Apple Health", auth_kind="device_push")
    scopes: list[str] = []

    def oauth_authorize_url(self, state: str) -> str:
        raise NotImplementedError("HealthKit uses device push, not OAuth")

    def exchange_code(self, code: str) -> TokenSet:
        raise NotImplementedError

    def refresh(self, token: TokenSet) -> TokenSet:
        raise NotImplementedError

    def sync(
        self,
        token: TokenSet,
        since: datetime | None,
        until: datetime | None = None,
    ) -> Iterable[NormalizedRecord]:
        # No-op: data arrives via POST /v1/ingest/healthkit, not server-pull.
        return iter(())
