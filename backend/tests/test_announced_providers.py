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
# Every direct source is announced for launch: Apple Health is the only
# route that has no approval to wait on and no per-app user cap.
ANNOUNCED = ("garmin", "wahoo", "oura", "strava", "google_health")


@pytest.mark.parametrize("slug", ANNOUNCED)
def test_listed_but_marked_unavailable(slug, client, auth):
    body = client.get("/v1/me", headers=auth).json()
    row = next(p for p in body["providers"] if p["slug"] == slug)
    assert row["available"] is False
    assert row["connected"] is False


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


def test_apple_health_leads_and_is_connectable(client, auth):
    """The only source everyone can use, so it goes first and stays live."""
    body = client.get("/v1/me", headers=auth).json()
    assert body["providers"][0]["slug"] == "healthkit"
    live = {p["slug"]: p for p in body["providers"] if p["available"]}
    assert "healthkit" in live


def test_a_direct_provider_needs_both_the_flag_and_credentials():
    """Credentials alone must not turn a Connect button on.

    Strava caps an unreviewed app at ten connected athletes and Oura at ten
    users, so a configured-but-unapproved provider going live publicly hands a
    403 to the eleventh person who installs the app.
    """
    from app.providers.google_health import GoogleHealthProvider

    p = get_provider("google_health")
    if settings().direct_providers_enabled and settings().google_health_client_id:
        assert isinstance(p, GoogleHealthProvider)
    else:
        assert p.info.availability == "coming_soon"


def test_the_connectable_source_comes_first():
    """A list that opens with things you cannot use reads as a broken product."""
    slugs = [p.info.slug for p in all_providers()]
    assert slugs[0] == "healthkit"
    assert min(slugs.index(s) for s in ANNOUNCED) > slugs.index("healthkit")


def test_closing_new_connections_does_not_break_existing_ones():
    """Someone who connected Strava last week still has a valid token and still
    expects their rides to arrive. The flag governs whether we hand out new
    connections, not whether we honour the ones already open."""
    from app.providers import get_sync_provider
    from app.providers.strava import StravaProvider

    if settings().strava_client_id:
        assert isinstance(get_sync_provider("strava"), StravaProvider)
    # And a source with no credentials at all still refuses, which is honest.
    assert get_sync_provider("garmin").info.availability == "coming_soon"
