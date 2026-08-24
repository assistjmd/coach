"""Adherence to the plan, not to a body.

Every wearable reports readiness. None of them prescribe anything, so none of
them can say whether you did it. This is the number that only works because the
coach made a call first.

The decisions worth pinning are about the denominator: which days count, and
which are excluded so the percentage keeps meaning something.
"""

from datetime import timedelta

from app.analytics.consistency import consistency
from app.dates import today_for
from app.models import DailyBrief, DayLog, User


def _user(db, tz="America/New_York"):
    u = User(email=f"c{id(db)}@example.com", timezone=tz)
    db.add(u)
    db.flush()
    return u


def _brief(db, user, days_ago, *, planned=True, auto=None, items=None):
    b = DailyBrief(
        user_id=user.id,
        day=today_for(user) - timedelta(days=days_ago),
        model="claude-sonnet-4-6",
        readiness_summary="x",
        training_prescription="y",
        session_planned=planned,
        compliance_auto=auto,
        session_items=items,
    )
    db.add(b)
    db.flush()
    return b


def test_rest_days_are_excluded_from_the_denominator(db):
    """Counted as wins, a twice-a-week trainer sits near 100% for doing little.
    Counted as losses, obeying a rest instruction is punished. Neither is right."""
    user = _user(db)
    for i in range(1, 4):
        _brief(db, user, i, planned=True, auto="complete")
    for i in range(4, 10):
        _brief(db, user, i, planned=False, auto="rest")
    db.commit()

    r = consistency(db, user)
    assert r.score == 100
    assert r.assessed == 3, "only prescribed sessions count"
    assert r.rest == 6


def test_partial_counts_as_half(db):
    user = _user(db)
    _brief(db, user, 1, auto="complete")
    _brief(db, user, 2, auto="partial")
    _brief(db, user, 3, auto="missed")
    db.commit()

    r = consistency(db, user)
    assert r.assessed == 3
    assert r.score == 50  # (1 + 0.5 + 0) / 3
    assert (r.completed, r.partial, r.missed) == (1, 1, 1)


def test_too_few_days_returns_no_score(db):
    """One completed session reading as 100% is noise, not a signal."""
    user = _user(db)
    _brief(db, user, 1, auto="complete")
    _brief(db, user, 2, auto="complete")
    db.commit()

    r = consistency(db, user)
    assert r.score is None
    assert r.assessed == 2


def test_days_with_no_brief_do_not_count(db):
    """The coach never made a call, so there is nothing to have followed."""
    user = _user(db)
    for i in range(1, 5):
        _brief(db, user, i, auto="complete")
    db.commit()

    r = consistency(db, user)
    assert r.assessed == 4, "the other ten days of the window have no brief"
    assert r.score == 100


def test_ticks_beat_inference(db):
    """The athlete telling us directly outranks a guess from training load."""
    user = _user(db)
    items = [{"id": "a", "text": "Squat 3x5"}, {"id": "b", "text": "Row 3x8"}]
    for i in (1, 2, 3):
        b = _brief(db, user, i, auto="missed", items=items)
        db.add(DayLog(user_id=user.id, day=b.day, completed_items=["a", "b"]))
    db.commit()

    assert consistency(db, user).score == 100


def test_override_beats_everything(db):
    user = _user(db)
    for i in (1, 2, 3):
        b = _brief(db, user, i, auto="missed")
        b.compliance_override = "complete"
    db.commit()

    assert consistency(db, user).score == 100


def test_streak_survives_a_rest_day(db):
    """Resting when told to is compliance, not a gap in it."""
    user = _user(db)
    _brief(db, user, 1, auto="complete")
    _brief(db, user, 2, planned=False, auto="rest")
    _brief(db, user, 3, auto="complete")
    db.commit()

    assert consistency(db, user).streak == 3


def test_streak_breaks_on_a_miss(db):
    user = _user(db)
    _brief(db, user, 1, auto="complete")
    _brief(db, user, 2, auto="missed")
    _brief(db, user, 3, auto="complete")
    db.commit()

    assert consistency(db, user).streak == 1


def test_an_untouched_today_does_not_zero_the_streak(db):
    """People open the app in the morning, before training. A streak that resets
    every day until you work out is worse than showing none."""
    user = _user(db)
    _brief(db, user, 0, auto="missed")  # today, not done yet
    _brief(db, user, 1, auto="complete")
    _brief(db, user, 2, auto="complete")
    db.commit()

    r = consistency(db, user)
    assert r.streak == 2
    # Today is excluded from the score for the same reason.
    assert r.missed == 0


def test_window_follows_the_athletes_timezone(db):
    """A Tokyo athlete's window must not be bounded by UTC's idea of today."""
    user = _user(db, tz="Asia/Tokyo")
    for i in (1, 2, 3):
        _brief(db, user, i, auto="complete")
    db.commit()

    assert consistency(db, user).score == 100


def test_no_data_at_all_is_not_an_error(db):
    user = _user(db)
    db.commit()
    r = consistency(db, user)
    assert r.score is None
    assert r.assessed == 0
    assert r.streak == 0
