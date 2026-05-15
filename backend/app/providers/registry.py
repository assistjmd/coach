from app.providers.base import Provider
from app.providers.healthkit import HealthKitProvider
from app.providers.oura import OuraProvider

_PROVIDERS: dict[str, Provider] = {
    "oura": OuraProvider(),
    "healthkit": HealthKitProvider(),
}


def get_provider(slug: str) -> Provider:
    if slug not in _PROVIDERS:
        raise KeyError(f"unknown provider: {slug}")
    return _PROVIDERS[slug]


def all_providers() -> list[Provider]:
    return list(_PROVIDERS.values())
