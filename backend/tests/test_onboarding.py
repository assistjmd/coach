"""Onboarding state and the goal contract it writes.

Onboarding completion is tracked server-side. A client-side flag would re-run
onboarding after a reinstall or on a second device for someone who already has a
goal, and gives no way to reset it deliberately.
"""

import pytest

from app.models import User


def test_new_user_is_not_onboarded(client, auth):
    body = client.get("/v1/me", headers=auth).json()
    assert body["onboarded"] is False
    assert body["timezone"] == "UTC"


def test_completing_onboarding_records_timezone(client, db, user, auth):
    r = client.post(
        "/v1/me/onboarding/complete", json={"timezone": "America/Chicago"}, headers=auth
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["onboarded"] is True
    assert body["timezone"] == "America/Chicago"

    db.expire_all()
    assert db.get(User, user.id).onboarded_at is not None


def test_onboarding_survives_a_new_session(client, auth):
    """The whole point of storing it server-side."""
    client.post("/v1/me/onboarding/complete", json={"timezone": "UTC"}, headers=auth)
    assert client.get("/v1/me", headers=auth).json()["onboarded"] is True


def test_completing_twice_keeps_the_original_timestamp(client, db, user, auth):
    client.post("/v1/me/onboarding/complete", json={"timezone": "UTC"}, headers=auth)
    db.expire_all()
    first = db.get(User, user.id).onboarded_at

    client.post("/v1/me/onboarding/complete", json={"timezone": "Europe/Paris"}, headers=auth)
    db.expire_all()
    fresh = db.get(User, user.id)
    assert fresh.onboarded_at == first, "re-completing must not restamp the original"
    assert fresh.timezone == "Europe/Paris", "but timezone should still update"


def test_unknown_timezone_is_rejected(client, auth):
    r = client.post("/v1/me/onboarding/complete", json={"timezone": "Mars/Olympus"}, headers=auth)
    assert r.status_code == 422


def test_onboarding_requires_auth(client):
    assert client.post("/v1/me/onboarding/complete", json={"timezone": "UTC"}).status_code == 401


# --- goal validation ---------------------------------------------------------


def test_race_without_a_target_date_is_rejected(client, auth):
    """A race goal with no date can't drive a training plan."""
    r = client.put("/v1/goal", json={"kind": "race", "sport": "running"}, headers=auth)
    assert r.status_code == 422
    assert "target_date" in r.text


def test_non_race_goals_do_not_need_a_date(client, auth):
    assert client.put("/v1/goal", json={"kind": "adaptive"}, headers=auth).status_code == 200


@pytest.mark.parametrize("kind", ["race", "improvement", "adaptive"])
def test_valid_kinds_are_accepted(client, auth, kind):
    payload = {"kind": kind}
    if kind == "race":
        payload["target_date"] = "2026-12-01"
    assert client.put("/v1/goal", json=payload, headers=auth).status_code == 200


def test_unknown_kind_is_rejected(client, auth):
    assert client.put("/v1/goal", json={"kind": "vibes"}, headers=auth).status_code == 422


def test_unknown_sport_is_rejected(client, auth):
    """Sport shares its vocabulary with workout data; free text would fragment it."""
    r = client.put("/v1/goal", json={"kind": "adaptive", "sport": "quidditch"}, headers=auth)
    assert r.status_code == 422


def test_put_returns_the_persisted_row(client, auth):
    """Not an echo of the request — the client should see what was stored."""
    r = client.put(
        "/v1/goal",
        json={"kind": "race", "sport": "trail_running", "target_date": "2026-10-01"},
        headers=auth,
    )
    assert r.json() == {
        "kind": "race",
        "sport": "trail_running",
        "target_date": "2026-10-01",
        "description": None,
        "params": None,
    }
