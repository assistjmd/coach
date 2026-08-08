from app.config import settings
from app.providers.announced import GARMIN, GOOGLE_HEALTH, WAHOO
from app.providers.base import Provider
from app.providers.google_health import GoogleHealthProvider
from app.providers.healthkit import HealthKitProvider
from app.providers.oura import OuraProvider
from app.providers.strava import StravaProvider
from app.providers.wahoo import WahooProvider

_PROVIDERS: dict[str, Provider] = {
    "oura": OuraProvider(),
    "strava": StravaProvider(),
    "healthkit": HealthKitProvider(),
    # Listed but not connectable yet. Registered rather than special-cased so
    # `get_provider` returns something the connect route can refuse cleanly,
    # instead of a 404 that reads like a bug.
    "garmin": GARMIN,
}

# Google Health goes live only once its credentials exist. Registering the real
# provider unconditionally would put a working Connect button in front of every
# user of an environment that has no client id, and the failure would land after
# they had already left the app for Google's consent screen.
_PROVIDERS["google_health"] = (
    GoogleHealthProvider() if settings().google_health_client_id else GOOGLE_HEALTH
)
_PROVIDERS["wahoo"] = (
    WahooProvider() if settings().wahoo_client_id else WAHOO
)

if settings().app_env == "dev":
    # Import lazily so the mock module isn't even loaded outside dev.
    from app.providers.mock import MockProvider

    _PROVIDERS["mock"] = MockProvider()


def get_provider(slug: str) -> Provider:
    if slug not in _PROVIDERS:
        raise KeyError(f"unknown provider: {slug}")
    return _PROVIDERS[slug]


def all_providers() -> list[Provider]:
    return list(_PROVIDERS.values())
