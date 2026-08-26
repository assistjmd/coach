"""Sleep fragments already in the table.

Ingestion was fixed in build 16, but rows written before it stayed fragmented —
and those are what the coach reads. One tester had 881 sleep records across 14
days, roughly 63 a night, which is both nonsense to reason about and 41,000
tokens of context resent on every chat message.
"""

from datetime import datetime, timedelta, timezone

from app.analytics.sleep_repair import collapse_nights
from app.models import SleepSession, User

BED = datetime(2026, 8, 24, 23, 0, tzinfo=timezone.utc)


def _user(db, email):
    u = User(email=email, timezone="UTC")
    db.add(u)
    db.flush()
    return u


def _frag(db, user, offset_min, minutes, source="healthkit", **stages):
    start = BED + timedelta(minutes=offset_min)
    s = SleepSession(
        user_id=user.id,
        source=source,
        source_record_id=f"{source}-{offset_min}",
        started_at=start,
        ended_at=start + timedelta(minutes=minutes),
        deep_s=stages.get("deep"),
        light_s=stages.get("light"),
        rem_s=stages.get("rem"),
        awake_s=stages.get("awake"),
        efficiency=stages.get("efficiency"),
    )
    db.add(s)
    db.flush()
    return s


def test_a_fragmented_night_becomes_one_row(db):
    user = _user(db, "frag@example.com")
    for i in range(12):
        _frag(db, user, i * 40, 40, deep=600 if i % 3 == 0 else None,
              light=1800 if i % 3 == 1 else None, rem=600 if i % 3 == 2 else None)
    db.commit()

    removed = collapse_nights(db, user.id)
    assert removed == 11

    night = db.query(SleepSession).filter(SleepSession.user_id == user.id).one()
    assert night.started_at == BED
    assert night.ended_at == BED + timedelta(minutes=480)
    # Stages are summed, so the totals survive the merge.
    assert night.deep_s == 2400
    assert night.light_s == 7200
    assert night.rem_s == 2400


def test_separate_nights_stay_separate(db):
    user = _user(db, "twonights@example.com")
    _frag(db, user, 0, 60)
    _frag(db, user, 60, 60)
    # Next night, a day later.
    _frag(db, user, 24 * 60, 60)
    db.commit()

    assert collapse_nights(db, user.id) == 1
    assert db.query(SleepSession).filter(SleepSession.user_id == user.id).count() == 2


def test_a_nap_is_not_folded_into_the_night(db):
    """Ninety minutes of daylight between them means two sleeps, not one."""
    user = _user(db, "nap@example.com")
    _frag(db, user, 0, 60)
    _frag(db, user, 60 + 120, 40)  # two hours later
    db.commit()

    assert collapse_nights(db, user.id) == 0
    assert db.query(SleepSession).filter(SleepSession.user_id == user.id).count() == 2


def test_two_devices_recording_one_night_are_not_summed(db):
    """An Oura night and an Apple Watch night describe the same sleep. Merging
    them would produce a fourteen-hour night."""
    user = _user(db, "twodevices@example.com")
    _frag(db, user, 0, 480, source="healthkit", deep=3600)
    _frag(db, user, 0, 480, source="oura", deep=3600)
    db.commit()

    assert collapse_nights(db, user.id) == 0
    assert db.query(SleepSession).filter(SleepSession.user_id == user.id).count() == 2


def test_missing_stages_stay_missing(db):
    """None means not measured. Summing it into zero would claim a measurement
    of no deep sleep, which is a different statement."""
    user = _user(db, "nostages@example.com")
    _frag(db, user, 0, 60)
    _frag(db, user, 60, 60)
    db.commit()

    collapse_nights(db, user.id)
    night = db.query(SleepSession).filter(SleepSession.user_id == user.id).one()
    assert night.deep_s is None


def test_repeat_runs_change_nothing(db):
    user = _user(db, "idem@example.com")
    for i in range(6):
        _frag(db, user, i * 40, 40, light=2400)
    db.commit()

    assert collapse_nights(db, user.id) == 5
    assert collapse_nights(db, user.id) == 0


def test_one_athletes_sleep_is_untouched_by_anothers_repair(db):
    a = _user(db, "a-sleep@example.com")
    b = _user(db, "b-sleep@example.com")
    for i in range(4):
        _frag(db, a, i * 40, 40)
        _frag(db, b, i * 40, 40)
    db.commit()

    collapse_nights(db, a.id)
    assert db.query(SleepSession).filter(SleepSession.user_id == b.id).count() == 4
