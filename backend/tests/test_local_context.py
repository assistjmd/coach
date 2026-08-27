"""The coach reasons in the athlete's clock, not UTC.

A session finished at 10pm Tuesday in Boston is 02:00 Wednesday in UTC. Handed
the raw timestamp, the coach described it as "early this morning" — correct for
the number, wrong for the person reading it.
"""

from datetime import datetime, timedelta, timezone

from app.ai.data import gather_recent
from app.models import HRVReading, SleepSession, User, Workout


def _user(db, tz="America/New_York"):
    u = User(email=f"tz{id(db)}@example.com", timezone=tz)
    db.add(u)
    db.flush()
    return u


def test_a_late_evening_session_is_not_this_morning(db):
    user = _user(db)
    # 10pm Tuesday in Boston, which is 02:00 Wednesday in UTC.
    db.add(Workout(
        user_id=user.id, source="healthkit", source_record_id="late",
        started_at=datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 27, 3, 18, tzinfo=timezone.utc),
        duration_s=4680, sport="strength", avg_hr=120,
    ))
    db.commit()

    w = gather_recent(db, user, days=14)["workouts"][0]
    assert w["started_at"].startswith("2026-08-26T22:00"), w["started_at"]
    assert "-04:00" in w["started_at"], "offset must be present, not stripped"


def test_the_timezone_is_stated_as_well_as_applied(db):
    """Belt and braces: the model has no reason to reach for UTC if the frame
    is named in the payload."""
    user = _user(db, tz="Asia/Tokyo")
    db.commit()
    snap = gather_recent(db, user, days=14)
    assert snap["timezone"] == "Asia/Tokyo"
    assert "local time" in snap["note"]
    assert snap["now"].endswith("+09:00")


def test_every_kind_of_timestamp_is_converted(db):
    user = _user(db)
    moment = datetime(2026, 8, 27, 2, 30, tzinfo=timezone.utc)
    db.add(HRVReading(user_id=user.id, source="healthkit", measured_at=moment, sdnn_ms=60))
    db.add(SleepSession(
        user_id=user.id, source="healthkit", source_record_id="s1",
        started_at=moment, ended_at=moment + timedelta(hours=7),
    ))
    db.commit()

    snap = gather_recent(db, user, days=14)
    assert snap["hrv"][0]["measured_at"].startswith("2026-08-26T22:30")
    # A sleep that began at 10:30pm belongs to that night, not the next day.
    assert snap["sleep"][0]["date"] == "2026-08-26"


def test_no_internal_keys_reach_the_model(db):
    """The raw datetimes are carried under private keys and must be stripped —
    leaking both would give the model two clocks to choose between."""
    user = _user(db)
    db.add(Workout(
        user_id=user.id, source="healthkit", source_record_id="w",
        started_at=datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc),
        duration_s=3600, sport="running",
    ))
    db.commit()

    assert "_started_at" not in str(gather_recent(db, user, days=14))


class TestUnreadableReplies:
    """A model answering with prose instead of JSON is a bad reply, not a
    server fault, and the athlete saw a 500 for it."""

    def test_adjust_raises_its_own_error(self, db, user, monkeypatch):
        import json as _json
        from types import SimpleNamespace

        from app.ai import adjust as adjust_mod
        from app.ai.adjust import UnreadableReply
        from app.models import DailyBrief
        from app.dates import today_for
        import pytest

        db.add(DailyBrief(
            user_id=user.id, day=today_for(user), model="m",
            readiness_summary="x", training_prescription="y", session_planned=True,
        ))
        db.commit()

        class _Messages:
            def create(self, **kw):
                return SimpleNamespace(content=[
                    SimpleNamespace(type="text", text="Sorry, I can't do that.")
                ])

        monkeypatch.setattr(
            adjust_mod.anthropic, "Anthropic",
            lambda **kw: SimpleNamespace(messages=_Messages()),
        )
        with pytest.raises(UnreadableReply):
            adjust_mod.adjust_session(db, user, "add more lifting")

    def test_the_endpoint_returns_502_not_500(self, client, auth, db, user, monkeypatch):
        from types import SimpleNamespace

        from app.ai import adjust as adjust_mod
        from app.models import DailyBrief
        from app.dates import today_for

        db.add(DailyBrief(
            user_id=user.id, day=today_for(user), model="m",
            readiness_summary="x", training_prescription="y", session_planned=True,
        ))
        db.commit()

        class _Messages:
            def create(self, **kw):
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="nope")])

        monkeypatch.setattr(
            adjust_mod.anthropic, "Anthropic",
            lambda **kw: SimpleNamespace(messages=_Messages()),
        )
        r = client.post("/v1/dashboard/today/adjust", headers=auth,
                        json={"message": "add more lifting"})
        assert r.status_code == 502
        assert "another way" in r.json()["detail"]
