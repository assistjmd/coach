"""Briefs must land in the user's own morning.

The job ran at a fixed 10:00 UTC for everyone, which is 3am in California and
8pm in Sydney. A "morning readiness brief" arriving in the middle of the night,
built from a day that hasn't happened yet, is worse than not sending one.
"""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.jobs.daily_brief import TARGET_LOCAL_HOUR, _is_target_hour, run_for_all_users
from app.models import DailyBrief, User, LegalConsent
from app.consent import CURRENT_LEGAL_VERSION

UTC = ZoneInfo("UTC")


@contextmanager
def _use(session):
    """Point the job's own SessionLocal at the test session.

    The `db` fixture holds everything inside a transaction it rolls back, so a
    fresh connection opened by the job would not see anything the test wrote.
    """
    yield session


def _user(tz: str) -> User:
    return User(timezone=tz)


def test_target_hour_is_evaluated_in_the_users_zone():
    # 13:00 UTC is 06:00 in Los Angeles.
    now = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)
    assert _is_target_hour(_user("America/Los_Angeles"), now)
    assert not _is_target_hour(_user("Europe/London"), now)
    assert not _is_target_hour(_user("Australia/Sydney"), now)


def test_every_zone_gets_its_turn_across_a_day():
    """Each user should hit the target hour exactly once in 24 hours."""
    for tz in ("America/Los_Angeles", "Europe/London", "Australia/Sydney", "Asia/Kolkata"):
        hits = [
            h for h in range(24)
            if _is_target_hour(_user(tz), datetime(2026, 8, 6, h, 0, tzinfo=UTC))
        ]
        assert len(hits) == 1, f"{tz} fired {len(hits)} times"


def test_unknown_timezone_falls_back_rather_than_exploding():
    """A malformed identifier must not take down the run for everyone else."""
    now = datetime(2026, 8, 6, TARGET_LOCAL_HOUR, 0, tzinfo=UTC)
    assert _is_target_hour(_user("Mars/Olympus_Mons"), now)


def test_half_hour_offset_zones_still_fire():
    """Kolkata is UTC+5:30 — an hour-granularity check must still match."""
    hits = [
        h for h in range(24)
        if _is_target_hour(_user("Asia/Kolkata"), datetime(2026, 8, 6, h, 30, tzinfo=UTC))
    ]
    assert len(hits) == 1


def test_generates_only_for_users_due_now(db):
    due = User(timezone="America/Los_Angeles", apple_sub="due")
    not_due = User(timezone="Australia/Sydney", apple_sub="not-due")
    db.add_all([due, not_due])
    db.commit()
    _slept(db, due)

    now = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)  # 06:00 in LA
    with patch("app.jobs.daily_brief.SessionLocal", lambda: _use(db)), \
         patch("app.jobs.daily_brief.generate_daily_brief") as gen:
        run_for_all_users(now=now)

    called_ids = {call.args[1].id for call in gen.call_args_list}
    assert due.id in called_ids
    assert not_due.id not in called_ids


def _slept(db, user, ended: datetime | None = None):
    """Give the user a night that ended this morning, their time."""
    from app.models import SleepSession
    _consent(db, user)

    ended = ended or datetime(2026, 8, 6, 12, 30, tzinfo=UTC)  # 05:30 in LA
    db.add(SleepSession(
        user_id=user.id, source="healthkit",
        source_record_id=f"night-{user.id}-{ended.isoformat()}",
        started_at=ended - timedelta(hours=7), ended_at=ended,
        light_s=20000,
    ))
    db.commit()


def _consent(db, user):
    user.date_of_birth = date(1990, 1, 1)
    db.add_all([LegalConsent(user_id=user.id, document=d, version=CURRENT_LEGAL_VERSION)
                for d in ("terms", "privacy")])
    db.commit()


class TestWaitsForLastNightsSleep:
    """A brief built before the night has synced silently uses the night before
    last. That is worse than no readiness at all, because it looks correct."""

    def test_it_waits_when_the_night_has_not_arrived(self, db):
        user = User(timezone="America/Los_Angeles", apple_sub="no-sleep-yet")
        db.add(user)
        db.commit()

        now = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)  # 06:00 in LA
        with patch("app.jobs.daily_brief.SessionLocal", lambda: _use(db)), \
             patch("app.jobs.daily_brief.generate_daily_brief") as gen:
            run_for_all_users(now=now)
        assert gen.call_args_list == [], "nothing should be written yet"

    def test_it_generates_once_the_night_lands(self, db):
        user = User(timezone="America/Los_Angeles", apple_sub="slept")
        db.add(user)
        db.commit()
        _slept(db, user)

        now = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)  # 08:00 in LA
        with patch("app.jobs.daily_brief.SessionLocal", lambda: _use(db)), \
             patch("app.jobs.daily_brief.generate_daily_brief") as gen:
            run_for_all_users(now=now)
        assert {c.args[1].id for c in gen.call_args_list} == {user.id}

    def test_the_deadline_writes_one_anyway(self, db):
        """Plenty of people do not track sleep. Waiting forever means they never
        get a brief at all."""
        user = User(timezone="America/Los_Angeles", apple_sub="never-sleeps")
        db.add(user)
        db.commit()
        _consent(db, user)

        now = datetime(2026, 8, 6, 18, 0, tzinfo=UTC)  # 11:00 in LA
        with patch("app.jobs.daily_brief.SessionLocal", lambda: _use(db)), \
             patch("app.jobs.daily_brief.generate_daily_brief") as gen:
            run_for_all_users(now=now)
        assert {c.args[1].id for c in gen.call_args_list} == {user.id}

    def test_nothing_runs_after_the_deadline(self, db):
        user = User(timezone="America/Los_Angeles", apple_sub="afternoon")
        db.add(user)
        db.commit()

        now = datetime(2026, 8, 6, 21, 0, tzinfo=UTC)  # 14:00 in LA
        with patch("app.jobs.daily_brief.SessionLocal", lambda: _use(db)), \
             patch("app.jobs.daily_brief.generate_daily_brief") as gen:
            run_for_all_users(now=now)
        assert gen.call_args_list == []

    def test_a_nap_is_not_a_night(self, db):
        """A forty minute doze after breakfast ends today too, and waiting for
        it would be waiting for something that already came."""
        user = User(timezone="America/Los_Angeles", apple_sub="napper")
        db.add(user)
        db.commit()
        _slept(db, user, ended=datetime(2026, 8, 6, 12, 30, tzinfo=UTC))
        from app.models import SleepSession

        db.query(SleepSession).filter(SleepSession.user_id == user.id).update(
            {"started_at": datetime(2026, 8, 6, 11, 55, tzinfo=UTC)}
        )
        db.commit()

        now = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)
        with patch("app.jobs.daily_brief.SessionLocal", lambda: _use(db)), \
             patch("app.jobs.daily_brief.generate_daily_brief") as gen:
            run_for_all_users(now=now)
        assert gen.call_args_list == []


def test_does_not_regenerate_an_existing_brief(db):
    """The hourly cadence can revisit the same local hour across a DST shift;
    regenerating would spend tokens overwriting an identical row."""
    user = User(timezone="America/Los_Angeles", apple_sub="already-has-one")
    db.add(user)
    db.commit()
    db.add(
        DailyBrief(
            user_id=user.id,
            day=datetime(2026, 8, 6, 6, tzinfo=ZoneInfo("America/Los_Angeles")).date(),
            readiness_summary="already here",
            training_prescription="p",
            model="test",
        )
    )
    db.commit()

    with patch("app.jobs.daily_brief.SessionLocal", lambda: _use(db)), \
         patch("app.jobs.daily_brief.generate_daily_brief") as gen:
        run_for_all_users(now=datetime(2026, 8, 6, 13, 0, tzinfo=UTC))

    assert gen.call_count == 0
