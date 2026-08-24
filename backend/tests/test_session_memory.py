"""The coach has to remember what it already asked for.

Without this it writes every brief from a blank slate — it cannot tell that it
prescribed back squats three sessions running, so it keeps prescribing them.
Provider data does not fill the gap: a Strava strength workout is a duration and
a heart rate, with no record of which lifts were done.
"""

from datetime import date, timedelta

from app.ai.brief import _MAX_SESSION_ITEMS, clean_items
from app.ai.data import gather_recent
from app.models import DailyBrief, DayLog, User


def _brief(user, day, items, **over):
    return DailyBrief(
        user_id=user.id,
        day=day,
        model="claude-sonnet-4-6",
        readiness_summary="x",
        training_prescription=over.get("prescription", "Lower body."),
        session_planned=over.get("planned", True),
        session_items=items,
    )


def test_recent_prescriptions_reach_the_model(db, user):
    today = date.today()
    db.add(_brief(user, today - timedelta(days=2), [
        {"id": "squat", "text": "Back squat, 4x5", "sets": 4, "reps": 5},
    ], prescription="Heavy squats."))
    db.commit()

    snap = gather_recent(db, user, days=14)
    sessions = snap["recent_sessions"]
    assert sessions, "the coach must be able to see what it already asked for"
    assert sessions[0]["prescribed"] == "Heavy squats."
    assert sessions[0]["items"][0]["text"] == "Back squat, 4x5"
    assert sessions[0]["items"][0]["prescribed_sets"] == 4


def test_what_they_actually_lifted_is_included(db, user):
    """Asked 3x8, did 4x6 means they went heavier. The next session should be
    programmed off the real numbers, not the request."""
    today = date.today()
    db.add(_brief(user, today - timedelta(days=1), [
        {"id": "press", "text": "Overhead press, 3x8", "sets": 3, "reps": 8},
    ]))
    db.add(DayLog(
        user_id=user.id,
        day=today - timedelta(days=1),
        completed_items=["press"],
        item_actuals={"press": {"sets": 4, "reps": 6}},
        journal="Felt strong, added weight.",
    ))
    db.commit()

    item = gather_recent(db, user, days=14)["recent_sessions"][0]["items"][0]
    assert item["completed"] is True
    assert item["actually_did"] == {"sets": 4, "reps": 6}
    assert "strong" in gather_recent(db, user, days=14)["recent_sessions"][0]["athlete_notes"]


def test_a_skipped_session_is_visible_as_skipped(db, user):
    """Repeating a session they skipped is correct. The coach can only know to
    do that if the untouched checklist is visible."""
    db.add(_brief(user, date.today() - timedelta(days=1), [
        {"id": "dl", "text": "Deadlift, 3x5", "sets": 3, "reps": 5},
    ]))
    db.commit()

    item = gather_recent(db, user, days=14)["recent_sessions"][0]["items"][0]
    assert item["completed"] is False


def test_older_sessions_fall_outside_the_window(db, user):
    db.add(_brief(user, date.today() - timedelta(days=45), [
        {"id": "old", "text": "Ancient history, 3x5"},
    ]))
    db.commit()
    assert gather_recent(db, user, days=14)["recent_sessions"] == []


def test_a_lifting_session_is_not_truncated_to_five_items():
    """Warm-up, four named exercises and a finisher is a normal lifting day.
    The old cap of five silently dropped the accessory work."""
    raw = [
        {"id": f"item{i}", "text": f"Exercise {i}, 3x8", "sets": 3, "reps": 8}
        for i in range(_MAX_SESSION_ITEMS)
    ]
    assert len(clean_items(raw)) == _MAX_SESSION_ITEMS == 8


def test_items_beyond_the_cap_are_still_dropped():
    raw = [{"id": f"i{i}", "text": f"Exercise {i}"} for i in range(20)]
    assert len(clean_items(raw)) == _MAX_SESSION_ITEMS


def test_no_sessions_yet_is_an_empty_list_not_an_error(db, user):
    assert gather_recent(db, user, days=14)["recent_sessions"] == []
