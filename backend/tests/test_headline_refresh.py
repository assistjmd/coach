"""Keeping the headline true as the day happens.

The brief is written in the morning and then contradicted by it: still saying
"get your run in" at 8pm to someone who ran at lunchtime. Every rewrite costs a
model call, so the bounds matter as much as the behaviour.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.dates import today_for
from app.jobs.daily_brief import MAX_REVISIONS, refresh_after_activity
from app.models import DailyBrief, SleepSession, User

NOW = datetime.now(timezone.utc)


def _brief(db, user, revision=0):
    b = DailyBrief(
        user_id=user.id, day=today_for(user), model="m",
        readiness_summary="x", training_prescription="y",
        session_planned=True, revision=revision,
    )
    db.add(b)
    db.commit()
    return b


def test_a_logged_session_updates_the_headline(db, user):
    """A refresh moves the read, not the plan — see test_headline_only.py."""
    _brief(db, user)
    from app.ai import headline as mod
    with patch.object(mod, "refresh_headline", return_value=True) as fn:
        assert refresh_after_activity(db, user) is True
        assert fn.call_count == 1


def test_rewrites_are_capped(db, user):
    """The only thing between a true headline and a sync loop spending money
    all afternoon."""
    _brief(db, user)
    from app.ai import headline as mod
    with patch.object(mod, "refresh_headline", return_value=True) as fn:
        for _ in range(10):
            refresh_after_activity(db, user)
        assert fn.call_count == MAX_REVISIONS


def test_nothing_happens_without_a_brief(db, user):
    from app.ai import headline as mod
    with patch.object(mod, "refresh_headline", return_value=True) as fn:
        assert refresh_after_activity(db, user) is False
        assert fn.call_count == 0


class TestIngestTriggers:
    def test_a_real_session_triggers_a_refresh(self, client, auth, db, user):
        _brief(db, user)
        payload = {"workouts": [{
            "source_record_id": "w1", "sport": "running",
            "started_at": (NOW - timedelta(hours=2)).isoformat(),
            "ended_at": (NOW - timedelta(hours=1)).isoformat(),
            "duration_s": 3600,
        }]}
        from app.ai import headline as mod
        with patch.object(mod, "refresh_headline", return_value=True) as fn:
            assert client.post("/v1/ingest/healthkit", headers=auth, json=payload).status_code == 200
            assert fn.call_count == 1

    def test_a_walk_to_the_shops_does_not(self, client, auth, db, user):
        """The headline changing because of an eight minute stroll reads as
        noise, and costs the same as a real one."""
        _brief(db, user)
        payload = {"workouts": [{
            "source_record_id": "w2", "sport": "walking",
            "started_at": (NOW - timedelta(minutes=30)).isoformat(),
            "ended_at": (NOW - timedelta(minutes=22)).isoformat(),
            "duration_s": 480,
        }]}
        from app.ai import headline as mod
        with patch.object(mod, "refresh_headline", return_value=True) as fn:
            assert client.post("/v1/ingest/healthkit", headers=auth, json=payload).status_code == 200
            assert fn.call_count == 0


class TestWakeTime:
    def test_the_wake_moment_comes_back_in_local_time(self, db, user, client, auth):
        # Anchored to the athlete's own morning rather than "two hours ago".
        # Run at 00:30 UTC, two hours ago is yesterday, and a sleep ending at
        # 22:30 last night is an evening nap rather than a night — which the
        # code is right to reject and the test was wrong to expect.
        from app.dates import today_for, zone_for

        tz = zone_for(user)
        morning = datetime.combine(today_for(user), datetime.min.time(), tzinfo=tz)
        end = morning + timedelta(hours=7)
        db.add(SleepSession(
            user_id=user.id, source="healthkit", source_record_id="n1",
            started_at=end - timedelta(hours=7), ended_at=end, light_s=20000,
        ))
        db.commit()

        woke = client.get("/v1/me", headers=auth).json()["woke_at"]
        assert woke is not None

    def test_it_is_null_before_the_night_syncs(self, client, auth):
        """Which is why a stated training hour exists as a fallback rather than
        as the only answer."""
        assert client.get("/v1/me", headers=auth).json()["woke_at"] is None

    def test_the_training_hour_round_trips(self, client, auth):
        body = client.put("/v1/me/training-hour", headers=auth,
                          json={"usual_training_hour": 7}).json()
        assert body["usual_training_hour"] == 7
        assert client.get("/v1/me", headers=auth).json()["usual_training_hour"] == 7

    def test_a_nonsense_hour_is_refused(self, client, auth):
        """This schedules a notification. A bad value fires one at an hour
        nobody wants."""
        assert client.put("/v1/me/training-hour", headers=auth,
                          json={"usual_training_hour": 47}).status_code == 422
