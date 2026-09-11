"""A brief written before the night synced gets exactly one rewrite.

Someone whose watch syncs at 8am should not spend the day reading readiness
computed from the night before last — but a sync that fires repeatedly must not
spend tokens in a loop either. The flag on the brief is what bounds it: once a
brief has the night, it is never regenerated again.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.analytics.overnight import last_night_arrived
from app.jobs.daily_brief import regenerate_if_sleep_landed
from app.models import DailyBrief, SleepSession, User, LegalConsent
from app.consent import CURRENT_LEGAL_VERSION

NOW = datetime.now(timezone.utc)


def _user(db, tz="America/New_York"):
    u = User(email=f"o{id(db)}@example.com", timezone=tz)
    db.add(u)
    db.flush()
    u.date_of_birth = date(1990, 1, 1)
    db.add_all([LegalConsent(user_id=u.id, document=d, version=CURRENT_LEGAL_VERSION)
                for d in ("terms", "privacy")])
    return u


def _night(db, user, hours=7, ends_hours_ago=2):
    from app.dates import today_for, zone_for
    # A local morning, independent of the wall-clock hour the suite runs at.
    end = datetime.combine(today_for(user), datetime.min.time(), tzinfo=zone_for(user))
    end += timedelta(hours=7 - (ends_hours_ago - 2))
    db.add(SleepSession(
        user_id=user.id, source="healthkit",
        source_record_id=f"n-{end.isoformat()}",
        started_at=end - timedelta(hours=hours), ended_at=end, light_s=20000,
    ))
    db.commit()


def _brief(db, user, *, had_sleep):
    from app.dates import today_for

    b = DailyBrief(
        user_id=user.id, day=today_for(user), model="m",
        readiness_summary="x", training_prescription="y",
        session_planned=True, had_overnight_sleep=had_sleep,
    )
    db.add(b)
    db.commit()
    return b


class TestDetection:
    def test_a_night_ending_today_counts(self, db):
        user = _user(db)
        _night(db, user)
        assert last_night_arrived(db, user) is True

    def test_nothing_at_all_does_not(self, db):
        assert last_night_arrived(db, _user(db)) is False

    def test_a_short_doze_does_not(self, db):
        user = _user(db)
        _night(db, user, hours=1)
        assert last_night_arrived(db, user) is False

    def test_the_night_before_last_does_not(self, db):
        """The exact bug: Friday's brief built from Wednesday night."""
        user = _user(db)
        _night(db, user, ends_hours_ago=30)
        assert last_night_arrived(db, user) is False


class TestRegeneration:
    def test_a_deadline_brief_is_rewritten_once_the_night_lands(self, db):
        user = _user(db)
        _brief(db, user, had_sleep=False)
        _night(db, user)

        with patch("app.ai.headline.refresh_headline", return_value=True) as gen:
            assert regenerate_if_sleep_landed(db, user) is True
            assert gen.call_count == 1

    def test_it_never_happens_twice(self, db):
        """A chatty sync must not spend tokens in a loop."""
        user = _user(db)
        _brief(db, user, had_sleep=False)
        _night(db, user)

        with patch("app.ai.headline.refresh_headline", return_value=True):
            assert regenerate_if_sleep_landed(db, user) is True
            assert regenerate_if_sleep_landed(db, user) is False
            assert regenerate_if_sleep_landed(db, user) is False

    def test_a_brief_that_already_had_the_night_is_left_alone(self, db):
        user = _user(db)
        _brief(db, user, had_sleep=True)
        _night(db, user)

        with patch("app.ai.headline.refresh_headline", return_value=True) as gen:
            assert regenerate_if_sleep_landed(db, user) is False
            assert gen.call_count == 0

    def test_nothing_happens_without_a_brief(self, db):
        user = _user(db)
        _night(db, user)
        with patch("app.ai.headline.refresh_headline", return_value=True) as gen:
            assert regenerate_if_sleep_landed(db, user) is False
            assert gen.call_count == 0

    def test_a_failed_regeneration_does_not_fail_the_upload(self, client, auth, db, user):
        """Ingest succeeded; the brief is a bonus. Losing the health data
        because a model call went wrong would be the worse outcome."""
        _brief(db, user, had_sleep=False)
        payload = {"sleep": [{
            "source_record_id": "n1",
            "started_at": (NOW - timedelta(hours=9)).isoformat(),
            "ended_at": (NOW - timedelta(hours=2)).isoformat(),
            "light_s": 20000,
        }]}
        with patch("app.ai.headline.refresh_headline", side_effect=RuntimeError("boom")):
            r = client.post("/v1/ingest/healthkit", headers=auth, json=payload)
        assert r.status_code == 200
        assert db.query(SleepSession).filter(SleepSession.user_id == user.id).count() == 1
