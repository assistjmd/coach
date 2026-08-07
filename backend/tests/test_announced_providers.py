"""Sources listed before they are connectable.

The whole point is that they appear in the list without pretending to work. Two
failure modes to hold off: a connect attempt that silently builds an OAuth URL
against a client id we don't have, and an announced provider leaking into the
sync path.
"""

import pytest

from app.config import settings
from app.providers import all_providers, get_provider
from app.providers.announced import NotYetAvailable

# Garmin only. Google Health flips to the real provider as soon as its
# credentials exist — see `test_google_health_is_announced_only_without_creds`.
ANNOUNCED = ("garmin",)


@pytest.mark.parametrize("slug", ANNOUNCED)
def test_listed_but_marked_unavailable(slug, client, auth):
    body = client.get("/v1/me", headers=auth).json()
    row = next(p for p in body["providers"] if p["slug"] == slug)
    assert row["available"] is False
    assert row["connected"] is False
    # The note is what the row actually renders. An empty one would leave the
    # user staring at a greyed-out source with no explanation.
    assert row["note"]


@pytest.mark.parametrize("slug", ANNOUNCED)
def test_connect_is_refused_with_a_reason(slug, client, auth):
    resp = client.post(f"/v1/providers/{slug}/authorize", headers=auth)
    assert resp.status_code == 409, "must not mint an authorize URL"
    assert resp.json()["detail"]


@pytest.mark.parametrize("slug", ANNOUNCED)
def test_every_operation_refuses(slug):
    """Defence in depth: even called directly, nothing half-works."""
    p = get_provider(slug)
    with pytest.raises(NotYetAvailable):
        p.oauth_authorize_url("state")
    with pytest.raises(NotYetAvailable):
        p.exchange_code("code")
    with pytest.raises(NotYetAvailable):
        p.sync(None, None)


def test_live_providers_are_unaffected(client, auth):
    body = client.get("/v1/me", headers=auth).json()
    live = {p["slug"]: p for p in body["providers"] if p["available"]}
    assert {"oura", "strava", "healthkit"} <= set(live)
    assert all(p["note"] is None for p in live.values())


def test_google_health_is_announced_only_without_creds():
    """The row must not offer Connect in an environment with no client id.

    Otherwise the failure lands after the user has already left the app for
    Google's consent screen, which reads as the app being broken rather than
    the integration not being configured.
    """
    from app.providers.announced import GOOGLE_HEALTH
    from app.providers.google_health import GoogleHealthProvider

    p = get_provider("google_health")
    if settings().google_health_client_id:
        assert isinstance(p, GoogleHealthProvider)
        assert p.info.availability == "live"
    else:
        assert p is GOOGLE_HEALTH
        assert p.info.availability == "coming_soon"


def test_announced_sort_after_live():
    """Real sources first. A list that opens with two things you can't use reads
    as a product that doesn't work."""
    slugs = [p.info.slug for p in all_providers()]
    first_announced = min(slugs.index(s) for s in ANNOUNCED)
    last_live = max(slugs.index(s) for s in ("oura", "strava", "healthkit"))
    assert last_live < first_announced
