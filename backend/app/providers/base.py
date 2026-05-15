"""Provider protocol — every cloud fitness service implements this interface.

Adding a new provider (Whoop, TrainingPeaks, Strava, ...): create a new module,
implement the protocol, and register it in `registry.py`. No core changes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

NormalizedKind = Literal[
    "workout",
    "sleep",
    "hrv",
    "heart_rate",
    "readiness",
    "body_metric",
]


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scope: str | None = None


@dataclass
class NormalizedRecord:
    kind: NormalizedKind
    source_record_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderInfo:
    slug: str
    display_name: str
    auth_kind: Literal["oauth2", "device_push"]


class Provider(Protocol):
    info: ProviderInfo
    scopes: list[str]

    def oauth_authorize_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> TokenSet: ...

    def refresh(self, token: TokenSet) -> TokenSet: ...

    def sync(
        self,
        token: TokenSet,
        since: datetime | None,
        until: datetime | None = None,
    ) -> Iterable[NormalizedRecord]: ...


def day_of(dt: datetime) -> date:
    return dt.date()
