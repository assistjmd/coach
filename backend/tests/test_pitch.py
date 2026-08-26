"""The case put to someone being asked to pay.

The rule that matters: nothing invented. Someone who connected nothing has no
case to make from their own data, and manufacturing one would be both dishonest
and immediately obvious to them.
"""

from datetime import date, datetime, timedelta, timezone

from app.analytics.pitch import build
from app.dates import today_for
from app.models import DailyBrief, DayLog, User, Workout

NOW = datetime.now(timezone.utc)


def _brief(db, user, days_ago, *, score=None, auto="complete", planned=True):
    b = DailyBrief(
        user_id=user.id,
        day=today_for(user) - timedelta(days=days_ago),
        model="m",
        readiness_summary="x",
        training_prescription="y",
        session_planned=planned,
        compliance_auto=auto,
        readiness_score=score,
    )
    db.add(b)
    db.flush()
    return b


def test_nothing_is_claimed_without_data(db, user):
    """A brand new account has no story, and the paywall falls back to plain
    copy rather than a fabricated one."""
    p = build(db, user)
    assert p.lines == []
    assert p.sessions_completed is None
    assert p.readiness_change is None


def test_completed_sessions_are_reported(db, user):
    for i in range(1, 6):
        _brief(db, user, i, auto="complete" if i > 2 else "missed")
    db.commit()

    p = build(db, user)
    assert p.sessions_asked == 5
    assert p.sessions_completed == 3
    assert "3 of the 5 sessions" in p.lines[0]


def test_too_few_sessions_says_nothing_about_them(db, user):
    """One completed session is not evidence, here or on the consistency card."""
    _brief(db, user, 1)
    db.commit()
    assert build(db, user).sessions_asked is None


def test_readiness_improvement_is_reported(db, user):
    for i, score in enumerate(reversed([50, 52, 54, 56, 62, 64, 66, 68]), start=1):
        _brief(db, user, i, score=score)
    db.commit()

    p = build(db, user)
    assert p.readiness_change is not None
    assert p.readiness_change > 0
    assert any("up" in line for line in p.lines)


def test_readiness_decline_is_not_hidden(db, user):
    """A number moving the wrong way is the best argument for keeping the coach,
    and showing it on the trend screen while omitting it here would be
    transparently selective."""
    for i, score in enumerate(reversed([70, 68, 66, 64, 55, 53, 51, 49]), start=1):
        _brief(db, user, i, score=score)
    db.commit()

    p = build(db, user)
    assert p.readiness_change < 0
    assert any("down" in line for line in p.lines)


def test_a_short_history_makes_no_readiness_claim(db, user):
    """Two readings can differ by more than any trend."""
    for i, score in enumerate([50, 80], start=1):
        _brief(db, user, i, score=score)
    db.commit()
    assert build(db, user).readiness_change is None


def test_logged_workouts_are_counted(db, user):
    # Anchored to the account rather than a module-level NOW, which is captured
    # at import and can precede created_at by a hair.
    start = user.created_at
    for i in range(3):
        db.add(Workout(
            user_id=user.id, source="healthkit", source_record_id=f"w{i}",
            started_at=start + timedelta(hours=i + 1),
            ended_at=start + timedelta(hours=i + 1, minutes=45),
            duration_s=2700, sport="running",
        ))
    db.commit()

    p = build(db, user)
    assert p.workouts_logged == 3
    assert any("3 sessions logged" in line for line in p.lines)


def test_backfilled_history_is_not_claimed_as_trial_activity(db, user):
    """Connecting Apple Health backfills ninety days. Counting that as sessions
    logged during the trial would tell someone who trained twice that they
    trained forty times, which they would notice."""
    db.add(Workout(
        user_id=user.id, source="healthkit", source_record_id="old",
        started_at=user.created_at - timedelta(days=60),
        ended_at=user.created_at - timedelta(days=60) + timedelta(hours=1),
        duration_s=3600, sport="running",
    ))
    db.commit()
    assert build(db, user).workouts_logged is None


def test_a_streak_is_mentioned_when_it_is_worth_mentioning(db, user):
    for i in range(1, 5):
        _brief(db, user, i, auto="complete")
    db.commit()
    assert any("days running" in line for line in build(db, user).lines)


def test_ticks_outrank_inference_here_too(db, user):
    """Same precedence chain as everywhere else, so the paywall cannot disagree
    with the consistency card the athlete has been looking at all fortnight."""
    items = [{"id": "a", "text": "Squat 3x5"}]
    for i in (1, 2, 3):
        b = _brief(db, user, i, auto="missed")
        b.session_items = items
        db.add(DayLog(user_id=user.id, day=b.day, completed_items=["a"]))
    db.commit()

    assert build(db, user).sessions_completed == 3


def test_the_status_endpoint_carries_the_pitch(client, auth, db, user):
    for i in range(1, 6):
        _brief(db, user, i)
    db.commit()

    body = client.get("/v1/subscriptions/status", headers=auth).json()
    assert "pitch" in body
    assert body["pitch"]["sessions_asked"] == 5
