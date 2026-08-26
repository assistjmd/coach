"""Which sources the app offers, and in what order.

Apple Health leads because it is the only one that works for everyone. The
direct integrations are all gated behind `direct_providers_enabled`, off for
launch: Strava stops an unreviewed app at ten connected athletes and Oura at ten
users, so shipping those buttons publicly would hand a 403 to the eleventh
person who installs the app. Everything they carry reaches us through Apple
Health instead.

When an approval lands, flip the flag and the real provider replaces the
announced one — the code for all of them is written, tested, and waiting.
"""

from app.config import settings
from app.providers.announced import (
    GARMIN,
    GOOGLE_HEALTH,
    OURA,
    STRAVA,
    WAHOO,
)
from app.providers.base import Provider
from app.providers.google_health import GoogleHealthProvider
from app.providers.healthkit import HealthKitProvider
from app.providers.oura import OuraProvider
from app.providers.strava import StravaProvider
from app.providers.wahoo import WahooProvider


def _live_or_announced(live_factory, announced, has_credentials: bool) -> Provider:
    """The real provider only when it is both enabled and configured.

    Registering a real provider without credentials would put a working Connect
    button in front of the user and fail after they had already left the app for
    a consent screen.
    """
    if settings().direct_providers_enabled and has_credentials:
        return live_factory()
    return announced


def _build() -> dict[str, Provider]:
    cfg = settings()
    providers: dict[str, Provider] = {
        # First, and the only one everybody can use.
        "healthkit": HealthKitProvider(),
        "oura": _live_or_announced(OuraProvider, OURA, bool(cfg.oura_client_id)),
        "strava": _live_or_announced(StravaProvider, STRAVA, bool(cfg.strava_client_id)),
        "garmin": GARMIN,
        "wahoo": _live_or_announced(WahooProvider, WAHOO, bool(cfg.wahoo_client_id)),
        "google_health": _live_or_announced(
            GoogleHealthProvider, GOOGLE_HEALTH, bool(cfg.google_health_client_id)
        ),
    }
    if cfg.app_env == "dev":
        # Imported lazily so the mock module isn't loaded outside dev.
        from app.providers.mock import MockProvider

        providers["mock"] = MockProvider()
    return providers


_PROVIDERS: dict[str, Provider] = _build()


def get_provider(slug: str) -> Provider:
    if slug not in _PROVIDERS:
        raise KeyError(f"unknown provider: {slug}")
    return _PROVIDERS[slug]


def all_providers() -> list[Provider]:
    return list(_PROVIDERS.values())
