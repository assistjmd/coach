"""The goal wire format.

`target_date` is a calendar date and must stay one on the wire. The iOS client
decodes it as a `String` in `yyyy-MM-dd` form precisely because coercing a race
day into a `Date` shifts it across timezones — a user in UTC-6 picking March 1
would round-trip to February 28.

This test pins the format that decoder depends on.
"""


def test_target_date_is_a_plain_calendar_date(client, auth):
    saved = client.put(
        "/v1/goal",
        json={"kind": "race", "sport": "running", "target_date": "2026-10-01"},
        headers=auth,
    )
    assert saved.status_code == 200, saved.text

    body = client.get("/v1/goal", headers=auth).json()
    assert body["target_date"] == "2026-10-01"
    assert "T" not in body["target_date"], "a date must not acquire a time component"


def test_goal_round_trips(client, auth):
    """The read path must return what the write path stored.

    Previously the write worked and the read silently failed to decode on the
    client, so a saved goal appeared to vanish.
    """
    payload = {
        "kind": "race",
        "sport": "running",
        "target_date": "2026-10-01",
        "description": "first marathon",
        "params": {"target_pace": "4:30/km"},
    }
    client.put("/v1/goal", json=payload, headers=auth)
    assert {k: v for k, v in client.get("/v1/goal", headers=auth).json().items()} == payload


def test_updating_a_goal_replaces_rather_than_duplicates(client, auth):
    client.put("/v1/goal", json={"kind": "adaptive"}, headers=auth)
    client.put("/v1/goal", json={"kind": "race", "target_date": "2027-01-01"}, headers=auth)
    body = client.get("/v1/goal", headers=auth).json()
    assert body["kind"] == "race"
    assert body["target_date"] == "2027-01-01"


def test_absent_goal_returns_null(client, auth):
    assert client.get("/v1/goal", headers=auth).json() is None


def test_goal_requires_auth(client):
    assert client.get("/v1/goal").status_code == 401
