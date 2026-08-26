"""One row per night per device.

The bug this replaces: a night reaches us in pieces because the client reads
Apple Health through an anchored query, and each row was keyed on whichever
fragment arrived first. Every sync that caught part of a night made another
night — five rows for one sleep — and stage totals were summed across those
re-reports until a 7.6 hour window claimed 15.2 hours of sleep.
"""

from datetime import datetime, timedelta, timezone

from app.analytics.sleep_repair import clamp_to_window, collapse_nights
from app.models import SleepSession, User

BED = datetime(2026, 8, 25, 22, 0, tzinfo=timezone.utc)


def _user(db, email):
    u = User(email=email, timezone="UTC")
    db.add(u)
    db.flush()
    return u


_seq = iter(range(10_000))


def _sleep(db, user, start_min, minutes, source="healthkit", **stages):
    start = BED + timedelta(minutes=start_min)
    row = SleepSession(
        user_id=user.id,
        source=source,
        # Distinct per row, as in production: each sync keys the night on
        # whichever fragment arrived first, which is how five rows for one
        # night got past the unique constraint.
        source_record_id=f"{source}-{next(_seq)}",
        started_at=start,
        ended_at=start + timedelta(minutes=minutes),
        deep_s=stages.get("deep"),
        light_s=stages.get("light"),
        rem_s=stages.get("rem"),
        awake_s=stages.get("awake"),
    )
    db.add(row)
    db.flush()
    return row


def test_the_same_night_reported_twice_is_not_doubled(db):
    """The 914-minute night. Two reports of one sleep take the max of each
    stage, never the sum — the second is a better view, not more sleep."""
    user = _user(db, "double@example.com")
    _sleep(db, user, 0, 456, deep=3600, light=18000, rem=5400)
    _sleep(db, user, 0, 456, deep=3600, light=18000, rem=5400)
    db.commit()

    assert collapse_nights(db, user.id) == 1
    night = db.query(SleepSession).filter(SleepSession.user_id == user.id).one()
    total_min = (night.deep_s + night.light_s + night.rem_s) / 60
    # 450 minutes of stages, not 900: the max of each stage, not the sum.
    assert total_min == 450
    assert total_min <= (night.ended_at - night.started_at).total_seconds() / 60


def test_five_identical_rows_become_one(db):
    user = _user(db, "five@example.com")
    for _ in range(5):
        _sleep(db, user, 0, 560, light=30000)
    db.commit()

    assert collapse_nights(db, user.id) == 4
    assert db.query(SleepSession).filter(SleepSession.user_id == user.id).count() == 1


def test_adjacent_fragments_still_add(db):
    """Two parts of one night describe different sleep, so these do sum."""
    user = _user(db, "frag@example.com")
    _sleep(db, user, 0, 120, light=7200)
    _sleep(db, user, 130, 120, light=7200)
    db.commit()

    collapse_nights(db, user.id)
    night = db.query(SleepSession).filter(SleepSession.user_id == user.id).one()
    assert night.light_s == 14400


def test_two_devices_are_never_merged(db):
    """An Oura night and an Apple Watch night are one sleep seen twice, and
    merging them would invent a fourteen-hour night."""
    user = _user(db, "devices@example.com")
    _sleep(db, user, 0, 456, source="healthkit", light=20000)
    _sleep(db, user, 0, 456, source="oura", light=20000)
    db.commit()

    assert collapse_nights(db, user.id) == 0
    assert db.query(SleepSession).filter(SleepSession.user_id == user.id).count() == 2


def test_a_nap_stays_its_own_sleep(db):
    user = _user(db, "nap2@example.com")
    _sleep(db, user, 0, 420, light=25000)
    _sleep(db, user, 420 + 120, 40, light=2400)
    db.commit()

    assert collapse_nights(db, user.id) == 0
    assert db.query(SleepSession).filter(SleepSession.user_id == user.id).count() == 2


def test_time_asleep_can_never_exceed_time_in_bed(db):
    """The physical backstop. If this fires something upstream double-counted,
    and a night bounded by the hours it occupied is the honest answer."""
    user = _user(db, "clamp@example.com")
    row = _sleep(db, user, 0, 456, deep=7200, light=36000, rem=10800)
    db.commit()

    assert clamp_to_window(row) is True
    total = row.deep_s + row.light_s + row.rem_s
    assert total <= 456 * 60
    # Proportions survive the scaling; only the magnitude changes.
    assert row.light_s > row.rem_s > row.deep_s


def test_a_plausible_night_is_left_alone(db):
    user = _user(db, "fine@example.com")
    row = _sleep(db, user, 0, 456, deep=3600, light=18000, rem=5400)
    db.commit()
    assert clamp_to_window(row) is False
    assert row.light_s == 18000


class TestIngest:
    def test_re_ingesting_a_night_does_not_create_a_second(self, client, auth, db, user):
        payload = {
            "sleep": [{
                "source_record_id": "frag-1",
                "started_at": BED.isoformat(),
                "ended_at": (BED + timedelta(minutes=456)).isoformat(),
                "deep_s": 3600, "light_s": 18000, "rem_s": 5400,
            }]
        }
        assert client.post("/v1/ingest/healthkit", headers=auth, json=payload).status_code == 200

        # The next sync carries the same night under a different fragment id,
        # which is exactly how the duplicates were being created.
        payload["sleep"][0]["source_record_id"] = "frag-2"
        assert client.post("/v1/ingest/healthkit", headers=auth, json=payload).status_code == 200

        rows = db.query(SleepSession).filter(SleepSession.user_id == user.id).all()
        assert len(rows) == 1
        assert (rows[0].deep_s + rows[0].light_s + rows[0].rem_s) / 60 == 450
