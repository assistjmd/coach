from app.config import settings
from app.providers.announced import GARMIN, GOOGLE_HEALTH
from app.providers.base import Provider
from app.providers.healthkit import HealthKitProvider
from app.providers.oura import OuraProvider
from app.providers.strava import StravaProvider

_PROVIDERS: dict[str, Provider] = {
    "oura": OuraProvider(),
    "strava": StravaProvider(),
    "healthkit": HealthKitProvider(),
    # Listed but not connectable yet. Registered rather than special-cased so
    # `get_provider` returns something the connect route can refuse cleanly,
    # instead of a 404 that reads like a bug.
    "garmin": GARMIN,
    "google_health": GOOGLE_HEALTH,
}

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
