"""Provider protocol — every cloud fitness service implements this interface.

Adding a new provider (Whoop, TrainingPeaks, Strava, ...): create a new module,
implement the protocol, and register it in `registry.py`. No core changes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

from app.config import settings

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
    # "coming_soon" means listed in the app but not connectable — see
    # `announced.py`. Defaulting to "live" keeps every existing provider
    # unchanged.
    availability: Literal["live", "coming_soon"] = "live"


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


def redirect_uri_for(slug: str) -> str:
    """The OAuth callback URL for a provider.

    Derived from `public_base_url` so adding a provider needs no new config key.
    A per-provider `<slug>_redirect_uri` setting still wins when set, for the
    case where a provider's registered URI can't match our route shape.

    The value must be byte-identical between the authorize request and the token
    exchange — OAuth2 requires it — so both sides call this rather than
    rebuilding the string.
    """
    cfg = settings()
    override = (getattr(cfg, f"{slug}_redirect_uri", "") or "").strip()
    if override:
        return override
    return f"{cfg.public_base_url.rstrip('/')}/v1/providers/{slug}/callback"
