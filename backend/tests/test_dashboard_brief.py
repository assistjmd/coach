"""Dashboard brief delivery.

The Anthropic client is stubbed throughout — asserting on model prose is a trap,
so these check only what we construct and store.

The behaviour under test: the nightly job runs at a fixed hour, so a user who
finishes onboarding after it has already run would otherwise see "no brief yet"
until the following day. That is the first screen after signup, so it has to
generate on demand instead.
"""

import json
from datetime import date, timedelta

import pytest

from app.dates import today_for
from app.models import DailyBrief, SleepSession


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, payload):
        self.content = [_Block(json.dumps(payload))]


@pytest.fixture
def fake_claude(monkeypatch):
    """Stub `messages.create`, recording calls so we can assert it wasn't wasted."""
    calls = []

    payload = {
        "readiness_summary": "HRV is steady and sleep was solid.",
        "training_prescription": "Easy 45 minutes, zone 2.",
        "rationale": "Recent load is moderate.",
        "flags": {"overreaching": False},
    }

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _Resp(payload)

    class _Client:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    monkeypatch.setattr("app.ai.brief.anthropic.Anthropic", _Client)
    return calls


def add_sleep(db, user, day: date) -> None:
    db.add(
        SleepSession(
            user_id=user.id,
            source="mock",
            source_record_id=f"s-{day}",
            started_at=f"{day}T22:00:00+00:00",
            ended_at=f"{day}T06:00:00+00:00",
            deep_s=4800,
            light_s=13200,
            rem_s=6600,
        )
    )
    db.commit()


def test_generates_on_demand_when_data_exists(client, db, user, auth, fake_claude):
    add_sleep(db, user, date.today())
    r = client.get("/v1/dashboard/today", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["readiness_summary"].startswith("HRV is steady")
    # The account's today, which is what the endpoint now resolves.
    assert body["day"] == today_for(user).isoformat()
    assert body["stale"] is False
    assert len(fake_claude) == 1
    # and it was persisted, not just returned
    assert db.query(DailyBrief).filter_by(user_id=user.id).count() == 1


def test_no_data_means_no_pointless_model_call(client, user, auth, fake_claude):
    """Generating a brief with nothing to reason about would burn tokens to say nothing."""
    r = client.get("/v1/dashboard/today", headers=auth)
    assert r.status_code == 404
    assert fake_claude == []


def test_existing_brief_for_today_is_not_regenerated(client, db, user, auth, fake_claude):
    add_sleep(db, user, date.today())
    first = client.get("/v1/dashboard/today", headers=auth)
    assert first.status_code == 200
    second = client.get("/v1/dashboard/today", headers=auth)
    assert second.status_code == 200
    assert len(fake_claude) == 1, "today's brief should be served from storage"


def test_falls_back_to_the_last_brief_when_generation_fails(
    client, db, user, auth, monkeypatch
):
    """An outage should show yesterday's brief, not a dead end."""
    yesterday = date.today() - timedelta(days=1)
    db.add(
        DailyBrief(
            user_id=user.id,
            day=yesterday,
            readiness_summary="Yesterday's read.",
            training_prescription="Rest.",
            model="test",
        )
    )
    add_sleep(db, user, yesterday)

    def boom(*a, **kw):
        raise RuntimeError("anthropic is down")

    monkeypatch.setattr("app.ai.brief.anthropic.Anthropic", boom)

    r = client.get("/v1/dashboard/today", headers=auth)
    assert r.status_code == 200
    assert r.json()["readiness_summary"] == "Yesterday's read."


def test_stale_flag_marks_an_old_brief(client, db, user, auth, monkeypatch):
    """The client must be able to tell 'today's' from 'the last one we managed'."""
    old = date.today() - timedelta(days=5)
    db.add(
        DailyBrief(
            user_id=user.id,
            day=old,
            readiness_summary="Old read.",
            training_prescription="Rest.",
            model="test",
        )
    )
    db.commit()

    def boom(*a, **kw):
        raise RuntimeError("anthropic is down")

    monkeypatch.setattr("app.ai.brief.anthropic.Anthropic", boom)

    body = client.get("/v1/dashboard/today", headers=auth).json()
    assert body["day"] == old.isoformat()
    assert body["stale"] is True


def test_dashboard_requires_auth(client):
    assert client.get("/v1/dashboard/today").status_code == 401
