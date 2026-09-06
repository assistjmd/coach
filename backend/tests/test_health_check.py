"""The daily check on things that fail silently.

Two outages have already happened this way: briefs stopped for two days when the
API balance ran out, and every provider connection broke for a day when a change
pointed the sync job at a placeholder. Neither moved a number anyone was
watching, and both were found by chance.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from app.jobs.health_check import run
from app.models import DailyBrief, OAuthToken, User, Workout
from app.dates import today_for

NOW = datetime.now(timezone.utc)


@contextmanager
def _use(session):
    """Point the job's own SessionLocal at the test session.

    The `db` fixture holds everything inside a transaction it rolls back, so a
    connection the job opens for itself would see none of it.
    """
    yield session


def _with_data(db, user):
    db.add(Workout(
        user_id=user.id, source="healthkit", source_record_id="w1",
        started_at=NOW.replace(hour=12), ended_at=NOW.replace(hour=13),
        duration_s=3600, sport="running",
    ))
    db.commit()


def test_silence_when_everything_is_working(db, user):
    """A daily all-clear is an email nobody opens, and an alert nobody opens is
    not an alert."""
    _with_data(db, user)
    db.add(DailyBrief(
        user_id=user.id, day=today_for(user), model="m",
        readiness_summary="x", training_prescription="y", session_planned=True,
    ))
    db.commit()

    with patch("app.alerts.send") as send:
        with patch("app.jobs.health_check.SessionLocal", lambda: _use(db)):
            assert run() == []
        assert send.call_count == 0


def test_no_briefs_anywhere_raises_the_alarm(db, user):
    _with_data(db, user)

    with patch("app.alerts.send") as send:
        with patch("app.jobs.health_check.SessionLocal", lambda: _use(db)):
            problems = run()
        assert len(problems) == 1
        assert "No briefs generated today" in problems[0]
        assert send.call_count == 1
        subject, body = send.call_args.args
        # Named, so the email says what to check rather than that something is wrong.
        assert "API balance" in body


def test_every_connection_failing_raises_the_alarm(db, user):
    _with_data(db, user)
    db.add(DailyBrief(
        user_id=user.id, day=today_for(user), model="m",
        readiness_summary="x", training_prescription="y", session_planned=True,
    ))
    db.add(OAuthToken(
        user_id=user.id, provider="strava",
        access_token_enc="x", refresh_token_enc="y",
        last_sync_error="NotYetAvailable: Strava is not connectable yet",
    ))
    db.commit()

    with patch("app.alerts.send") as send:
        with patch("app.jobs.health_check.SessionLocal", lambda: _use(db)):
            problems = run()
        assert any("connections are failing" in p for p in problems)
        assert send.call_count == 1


def test_one_broken_connection_is_not_an_outage(db, user):
    """A single revoked token is that athlete's problem to reconnect, not a
    reason to wake the operator."""
    _with_data(db, user)
    db.add(DailyBrief(
        user_id=user.id, day=today_for(user), model="m",
        readiness_summary="x", training_prescription="y", session_planned=True,
    ))
    db.add(OAuthToken(user_id=user.id, provider="strava",
                      access_token_enc="x", last_sync_error="revoked"))
    db.add(OAuthToken(user_id=user.id, provider="oura",
                      access_token_enc="x", last_sync_error=None))
    db.commit()

    with patch("app.alerts.send") as send:
        with patch("app.jobs.health_check.SessionLocal", lambda: _use(db)):
            assert run() == []
        assert send.call_count == 0


def test_a_failing_send_does_not_break_the_check(db, user):
    """The alert failing must not stop the job that noticed the problem."""
    from app import alerts

    _with_data(db, user)
    with patch("smtplib.SMTP_SSL", side_effect=OSError("no network")):
        assert alerts.send("x", "y") is False
    with patch("app.jobs.health_check.SessionLocal", lambda: _use(db)):
        assert run()  # still reports the problem


def test_an_unconfigured_mailbox_degrades_quietly(db, user, monkeypatch):
    """A missing password must not take the scheduler down with it."""
    from app import alerts
    from app.config import settings

    monkeypatch.setattr(settings(), "alert_email_to", "", raising=False)
    assert alerts.configured() is False
    assert alerts.send("subject", "body") is False
