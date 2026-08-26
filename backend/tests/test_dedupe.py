"""One session, several sources.

A strength session can reach us five times: Strava's API, plus separate
HKWorkout records written into Apple Health by Garmin Connect, the Strava app
and the Watch. Every one carries a different UUID, so the per-source unique
constraint lets them all through, and daily_loads then sums five copies of the
same 41 minutes into roughly five times the real training load.
"""

from datetime import datetime, timedelta, timezone

from app.analytics.dedupe import better, collapse_existing, find_duplicate
from app.analytics.load import daily_loads
from app.models import User, Workout

NOON = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _add(db, user, source, rec_id, start, minutes, **over):
    w = Workout(
        user_id=user.id,
        source=source,
        source_record_id=rec_id,
        started_at=start,
        ended_at=start + timedelta(minutes=minutes),
        duration_s=minutes * 60,
        sport=over.get("sport", "strength"),
        avg_hr=over.get("avg_hr"),
        max_hr=over.get("max_hr"),
        distance_m=over.get("distance_m"),
    )
    db.add(w)
    db.flush()
    return w


def _user(db, email):
    u = User(email=email, timezone="UTC")
    db.add(u)
    db.flush()
    return u


def test_same_session_from_two_sources_is_one_workout(db):
    """41 and 42 minutes, a minute apart, is one lift recorded twice."""
    user = _user(db, "dupe@example.com")
    _add(db, user, "strava", "s1", NOON, 42, avg_hr=87)
    match = find_duplicate(db, user.id, NOON + timedelta(minutes=1),
                           NOON + timedelta(minutes=42))
    assert match is not None
    assert match.source == "strava"


def test_back_to_back_sessions_are_kept_apart(db):
    """A brick is a bike then a run. Merging them would erase a real session."""
    user = _user(db, "brick@example.com")
    _add(db, user, "strava", "bike", NOON, 60, sport="cycling")
    run_start = NOON + timedelta(minutes=61)
    assert find_duplicate(db, user.id, run_start, run_start + timedelta(minutes=30)) is None


def test_a_short_overlap_is_not_a_duplicate(db):
    """Cooling down as the next session starts is not the same session."""
    user = _user(db, "overlap@example.com")
    _add(db, user, "strava", "long", NOON, 60)
    late = NOON + timedelta(minutes=55)
    assert find_duplicate(db, user.id, late, late + timedelta(minutes=60)) is None


def test_heart_rate_wins_over_source_rank(db):
    """HR decides TRIMP versus the duration estimate, so it outranks provenance."""
    user = _user(db, "hr@example.com")
    plain_strava = Workout(source="strava", sport="strength", avg_hr=None)
    healthkit_hr = Workout(source="healthkit", sport="strength", avg_hr=140)
    assert better(healthkit_hr, plain_strava)
    assert not better(plain_strava, healthkit_hr)


def test_collapse_repairs_history_and_the_load(db):
    """The screenshot case: one Strava row and four HealthKit copies."""
    user = _user(db, "history@example.com")
    _add(db, user, "strava", "s1", NOON, 42, avg_hr=87)
    for i in range(4):
        _add(db, user, "healthkit", f"hk{i}",
             NOON + timedelta(seconds=30 * i), 41, avg_hr=87)
    db.commit()

    day = NOON.date()
    inflated = daily_loads(db, user.id, day, day)[day][0]

    removed = collapse_existing(db, user.id)
    assert removed == 4
    assert db.query(Workout).filter(Workout.user_id == user.id).count() == 1

    corrected = daily_loads(db, user.id, day, day)[day][0]
    assert corrected < inflated / 3, "load must fall to roughly one session"


def test_collapse_is_safe_to_run_twice(db):
    user = _user(db, "idempotent@example.com")
    _add(db, user, "strava", "s1", NOON, 42, avg_hr=87)
    _add(db, user, "healthkit", "hk1", NOON, 41, avg_hr=87)
    db.commit()

    assert collapse_existing(db, user.id) == 1
    assert collapse_existing(db, user.id) == 0


def test_one_athletes_duplicates_do_not_touch_another(db):
    a = _user(db, "a@example.com")
    b = _user(db, "b@example.com")
    _add(db, a, "strava", "s1", NOON, 42)
    _add(db, b, "strava", "s2", NOON, 42)
    db.commit()

    collapse_existing(db, a.id)
    assert db.query(Workout).filter(Workout.user_id == b.id).count() == 1


def test_iso_strings_are_accepted(db):
    """Provider payloads carry text, not datetimes. Oura passes bedtime_start
    straight through and Google Health sends RFC-3339, so this path has to take
    both or it crashes on the first real sync."""
    user = _user(db, "iso@example.com")
    _add(db, user, "strava", "s1", NOON, 42, avg_hr=87)
    db.commit()

    match = find_duplicate(
        db, user.id, "2026-08-24T12:00:30Z", "2026-08-24T12:41:00Z"
    )
    assert match is not None
    # Offsets other than Z must work too.
    assert find_duplicate(
        db, user.id, "2026-08-24T08:00:30-04:00", "2026-08-24T08:41:00-04:00"
    ) is not None


class TestProvenanceSurvives:
    """Dedup used to delete the losing records outright, throwing away the most
    defensible thing we know about a session: that three devices saw it and we
    kept the one with power data. That evidence is the substance behind
    reconciling across sources."""

    def test_every_source_that_saw_it_is_remembered(self, db):
        user = _user(db, "prov@example.com")
        _add(db, user, "strava", "s1", NOON, 42, avg_hr=87)
        _add(db, user, "healthkit", "hk1", NOON, 41, avg_hr=87)
        _add(db, user, "oura", "o1", NOON, 41)
        db.commit()

        collapse_existing(db, user.id)
        w = db.query(Workout).filter(Workout.user_id == user.id).one()
        assert set(w.contributing_sources) == {"strava", "healthkit", "oura"}

    def test_the_kept_source_is_still_identifiable(self, db):
        """Which one won matters as much as which ones agreed."""
        user = _user(db, "kept@example.com")
        _add(db, user, "healthkit", "hk1", NOON, 41)
        _add(db, user, "strava", "s1", NOON, 42, avg_hr=150, distance_m=8000)
        db.commit()

        collapse_existing(db, user.id)
        w = db.query(Workout).filter(Workout.user_id == user.id).one()
        assert w.source == "strava", "the richer record wins"
        assert set(w.contributing_sources) == {"strava", "healthkit"}

    def test_a_single_source_session_lists_only_itself(self, db):
        user = _user(db, "solo@example.com")
        _add(db, user, "healthkit", "hk1", NOON, 41)
        db.commit()
        collapse_existing(db, user.id)
        w = db.query(Workout).filter(Workout.user_id == user.id).one()
        assert w.contributing_sources in (None, ["healthkit"])

    def test_the_list_is_stable_between_runs(self, db):
        """Read back and shown to the athlete, so an order that shifts between
        syncs would look like the data changed when it did not."""
        user = _user(db, "stable@example.com")
        _add(db, user, "strava", "s1", NOON, 42, avg_hr=87)
        _add(db, user, "healthkit", "hk1", NOON, 41, avg_hr=87)
        db.commit()

        collapse_existing(db, user.id)
        first = list(db.query(Workout).filter(Workout.user_id == user.id).one().contributing_sources)
        collapse_existing(db, user.id)
        assert db.query(Workout).filter(Workout.user_id == user.id).one().contributing_sources == first
