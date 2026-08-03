"""The provider connect flow.

The rule under test: once the provider has redirected the user back to us, every
path terminates in a `coach://` redirect. The user is inside an
`ASWebAuthenticationSession` web view, which only closes when it sees that
scheme — an HTTPException there strands them on a JSON dump.
"""

import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from app.auth.jwt import issue_state_token
from app.models import OAuthStateUse, OAuthToken
from app.providers import registry
from app.providers.base import ProviderInfo, TokenSet

SLUG = "faketracker"


class FakeProvider:
    info = ProviderInfo(slug=SLUG, display_name="Fake Tracker", auth_kind="oauth2")
    scopes = ["read"]

    def __init__(self):
        self.exchange_calls: list[str] = []

    def oauth_authorize_url(self, state: str) -> str:
        return f"https://fake.example/authorize?state={urllib.parse.quote(state)}"

    def exchange_code(self, code: str) -> TokenSet:
        self.exchange_calls.append(code)
        if code != "good-code":
            raise ValueError("bad code")
        return TokenSet(
            access_token="at",
            refresh_token="rt",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scope="read",
        )

    def refresh(self, token: TokenSet) -> TokenSet:
        return token

    def sync(self, token, since, until=None):
        return iter(())


@pytest.fixture
def provider():
    p = FakeProvider()
    registry._PROVIDERS[SLUG] = p
    yield p
    registry._PROVIDERS.pop(SLUG, None)


def deep_link(response) -> dict[str, str]:
    """Assert the response is a redirect into our scheme; return its params."""
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith("coach://oauth/callback"), location
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(location).query).items()}


def state_for(user) -> str:
    return issue_state_token(user.id, SLUG)[0]


# --- authorize ---------------------------------------------------------------


def test_authorize_requires_auth(client, provider):
    assert client.post(f"/v1/providers/{SLUG}/authorize").status_code == 401


def test_authorize_returns_a_url_carrying_state(client, auth, provider):
    r = client.post(f"/v1/providers/{SLUG}/authorize", headers=auth)
    assert r.status_code == 200
    body = r.json()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(body["authorize_url"]).query)
    assert query["state"][0]
    assert body["expires_at"]


def test_authorize_rejects_unknown_provider(client, auth):
    assert client.post("/v1/providers/nope/authorize", headers=auth).status_code == 404


def test_authorize_rejects_device_push_provider(client, auth):
    assert client.post("/v1/providers/healthkit/authorize", headers=auth).status_code == 400


# --- callback: success -------------------------------------------------------


def test_callback_persists_token_and_redirects(client, db, user, provider):
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code", "state": state_for(user)},
        follow_redirects=False,
    )
    params = deep_link(r)
    assert params["status"] == "ok"
    assert params["provider"] == SLUG

    row = db.query(OAuthToken).filter_by(user_id=user.id, provider=SLUG).one()
    assert row.access_token_enc  # stored encrypted, not in the clear
    assert row.access_token_enc != "at"


def test_callback_reconnect_updates_the_existing_row(client, db, user, provider):
    for _ in range(2):
        r = client.get(
            f"/v1/providers/{SLUG}/callback",
            params={"code": "good-code", "state": state_for(user)},
            follow_redirects=False,
        )
        assert deep_link(r)["status"] == "ok"
    assert db.query(OAuthToken).filter_by(user_id=user.id, provider=SLUG).count() == 1


# --- callback: failure paths -------------------------------------------------


def test_denied_consent_is_a_deep_link_not_a_422(client, user, provider):
    """The trap: `code` declared required turns a Deny tap into a validation dump."""
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"error": "access_denied", "state": state_for(user)},
        follow_redirects=False,
    )
    params = deep_link(r)
    assert params["status"] == "error"
    assert params["reason"] == "denied"
    assert params["ref"]


def test_replayed_state_is_rejected(client, db, user, provider):
    state = state_for(user)
    first = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert deep_link(first)["status"] == "ok"

    second = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert deep_link(second)["reason"] == "invalid_state"
    assert db.query(OAuthStateUse).count() == 1


def test_state_is_spent_even_when_the_user_denies(client, user, provider):
    """A denied state must not be reusable for a later forged approval."""
    state = state_for(user)
    client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    retry = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert deep_link(retry)["reason"] == "invalid_state"


def test_garbage_state_is_rejected(client, provider):
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code", "state": "not-a-jwt"},
        follow_redirects=False,
    )
    assert deep_link(r)["reason"] == "invalid_state"


def test_missing_state_is_rejected(client, provider):
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code"},
        follow_redirects=False,
    )
    assert deep_link(r)["reason"] == "invalid_state"


def test_state_bound_to_another_provider_is_rejected(client, user, provider):
    """A state minted for one provider must not authorise a different one."""
    other, _ = issue_state_token(user.id, "oura")
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code", "state": other},
        follow_redirects=False,
    )
    assert deep_link(r)["reason"] == "invalid_state"


def test_failed_exchange_reports_exchange_failed(client, db, user, provider):
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "wrong-code", "state": state_for(user)},
        follow_redirects=False,
    )
    assert deep_link(r)["reason"] == "exchange_failed"
    assert db.query(OAuthToken).filter_by(provider=SLUG).count() == 0


def test_no_code_and_no_error_does_not_crash(client, user, provider):
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"state": state_for(user)},
        follow_redirects=False,
    )
    assert deep_link(r)["reason"] == "exchange_failed"


def test_provider_error_text_is_never_forwarded(client, user, provider):
    """`reason` is a closed enum — provider strings must not reach our UI."""
    r = client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"error": "<script>alert(1)</script>", "state": state_for(user)},
        follow_redirects=False,
    )
    params = deep_link(r)
    assert params["reason"] in {"denied", "invalid_state", "exchange_failed", "server_error"}
    assert "script" not in r.headers["location"]


# --- disconnect --------------------------------------------------------------


def test_disconnect_removes_the_token(client, db, user, auth, provider):
    client.get(
        f"/v1/providers/{SLUG}/callback",
        params={"code": "good-code", "state": state_for(user)},
        follow_redirects=False,
    )
    assert client.delete(f"/v1/providers/{SLUG}", headers=auth).status_code == 204
    assert db.query(OAuthToken).filter_by(user_id=user.id, provider=SLUG).count() == 0


def test_disconnect_requires_auth(client, provider):
    assert client.delete(f"/v1/providers/{SLUG}").status_code == 401
