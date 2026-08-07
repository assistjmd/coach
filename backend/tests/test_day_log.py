"""Journal entries and session checkboxes.

A tick is the athlete telling us directly what happened, which outranks matching
a workout against a prose prescription. The risk is the opposite of the
inference bug: a checklist that loses ticks, or counts ones for items nobody can
see, quietly corrupts the record it exists to make trustworthy.
"""

from datetime import date

from app.analytics.compliance import from_ticks
from app.models import DailyBrief, DayLog

DAY = date(2026, 8, 6)
ITEMS = [
    {"id": "warmup", "text": "10 min easy"},
    {"id": "main", "text": "3x8 at threshold"},
    {"id": "cooldown", "text": "10 min easy"},
]


def _brief(db, user, **kw) -> DailyBrief:
    b = DailyBrief(
        user_id=user.id,
        day=DAY,
        readiness_summary="s",
        training_prescription="p",
        model="test",
        session_planned=True,
        **{"session_items": ITEMS, **kw},
    )
    db.add(b)
    db.commit()
    return b


# --- tick semantics -----------------------------------------------------


def test_all_items_ticked_is_complete():
    b = DailyBrief(session_items=ITEMS, session_planned=True)
    assert from_ticks(b, ["warmup", "main", "cooldown"]) == "complete"


def test_some_items_ticked_is_partial():
    b = DailyBrief(session_items=ITEMS, session_planned=True)
    assert from_ticks(b, ["warmup"]) == "partial"


def test_nothing_ticked_defers_to_inference():
    """An untouched checklist is not a confession — most people never tick."""
    b = DailyBrief(session_items=ITEMS, session_planned=True)
    assert from_ticks(b, []) is None
    assert from_ticks(b, None) is None


def test_stale_ids_do_not_count():
    """A regenerated brief can drop an item; a leftover tick must not inflate
    the completion count against a checkbox nobody can see."""
    b = DailyBrief(session_items=ITEMS, session_planned=True)
    assert from_ticks(b, ["deleted_item"]) is None


def test_no_items_means_nothing_to_read():
    assert from_ticks(DailyBrief(session_items=None), ["x"]) is None


# --- endpoint -----------------------------------------------------------


def test_saving_a_journal_does_not_clear_the_ticks(client, db, user, auth):
    _brief(db, user)
    client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"completed_items": ["warmup", "main"]},
        headers=auth,
    )
    resp = client.put(
        f"/v1/insights/day/{DAY}/log", json={"journal": "felt good"}, headers=auth
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["journal"] == "felt good"
    # The bug this guards: a partial update wiping the field it didn't mention.
    assert set(body["completed_items"]) == {"warmup", "main"}


def test_ticks_beat_inference(client, db, user, auth):
    """No workout logged, but the athlete says they did the session."""
    _brief(db, user)
    resp = client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"completed_items": ["warmup", "main", "cooldown"]},
        headers=auth,
    )
    assert resp.json()["compliance"] == "complete"


def test_explicit_correction_still_beats_ticks(client, db, user, auth):
    _brief(db, user)
    client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"completed_items": ["warmup", "main", "cooldown"]},
        headers=auth,
    )
    resp = client.patch(
        f"/v1/insights/day/{DAY}", json={"compliance": "missed"}, headers=auth
    )
    assert resp.json()["compliance"] == "missed"


def test_unknown_ids_are_rejected_on_write(client, db, user, auth):
    _brief(db, user)
    resp = client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"completed_items": ["warmup", "not_a_real_item"]},
        headers=auth,
    )
    assert resp.json()["completed_items"] == ["warmup"]


def test_journal_works_on_a_day_with_no_brief(client, db, user, auth):
    """A session done before installing the app still deserves a note."""
    resp = client.put(
        "/v1/insights/day/2026-07-01/log",
        json={"journal": "ran before I had the app"},
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["journal"] == "ran before I had the app"


def test_clearing_the_journal(client, db, user, auth):
    _brief(db, user)
    client.put(f"/v1/insights/day/{DAY}/log", json={"journal": "x"}, headers=auth)
    resp = client.put(f"/v1/insights/day/{DAY}/log", json={"journal": "  "}, headers=auth)
    assert resp.json()["journal"] is None


def test_log_appears_in_the_calendar(client, db, user, auth):
    _brief(db, user)
    client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"journal": "hard but fine", "completed_items": ["main"]},
        headers=auth,
    )
    body = client.get(
        f"/v1/insights/calendar?start={DAY}&end={DAY}", headers=auth
    ).json()
    day = body["days"][0]
    assert day["journal"] == "hard but fine"
    assert day["completed_items"] == ["main"]
    assert len(day["session_items"]) == 3
    assert day["compliance"] == "partial"


def test_one_log_row_per_day(client, db, user, auth):
    _brief(db, user)
    for note in ("first", "second", "third"):
        client.put(f"/v1/insights/day/{DAY}/log", json={"journal": note}, headers=auth)
    rows = db.query(DayLog).filter(DayLog.user_id == user.id, DayLog.day == DAY).count()
    assert rows == 1


# --- actual sets and reps ------------------------------------------------

COUNTED = [
    {"id": "main", "text": "3x8 back squat", "sets": 3, "reps": 8},
    {"id": "easy", "text": "20 min easy"},
]


def test_prescribed_counts_are_returned(client, db, user, auth):
    _brief(db, user, session_items=COUNTED)
    day = client.get(f"/v1/insights/calendar?start={DAY}&end={DAY}",
                     headers=auth).json()["days"][0]
    items = {i["id"]: i for i in day["session_items"]}
    assert items["main"]["sets"] == 3
    assert items["main"]["reps"] == 8
    # An uncountable item must not get invented numbers — their absence is what
    # tells the client not to offer a stepper.
    assert items["easy"]["sets"] is None
    assert items["easy"]["reps"] is None


def test_logging_a_different_number_of_sets(client, db, user, auth):
    _brief(db, user, session_items=COUNTED)
    resp = client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"item_actuals": {"main": {"sets": 4, "reps": 6}}},
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["item_actuals"]["main"] == {"sets": 4, "reps": 6}


def test_actuals_only_exist_where_something_differed(client, db, user, auth):
    """No entry means "as prescribed". Storing empties would make that
    indistinguishable from a deviation."""
    _brief(db, user, session_items=COUNTED)
    resp = client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"item_actuals": {"main": {}}},
        headers=auth,
    )
    assert resp.json()["item_actuals"] == {}


def test_actuals_for_unknown_items_are_dropped(client, db, user, auth):
    _brief(db, user, session_items=COUNTED)
    resp = client.put(
        f"/v1/insights/day/{DAY}/log",
        json={"item_actuals": {"main": {"sets": 4}, "ghost": {"sets": 9}}},
        headers=auth,
    )
    body = resp.json()["item_actuals"]
    assert "main" in body and "ghost" not in body


def test_actuals_survive_a_journal_save(client, db, user, auth):
    _brief(db, user, session_items=COUNTED)
    client.put(f"/v1/insights/day/{DAY}/log",
               json={"item_actuals": {"main": {"sets": 5}}}, headers=auth)
    resp = client.put(f"/v1/insights/day/{DAY}/log",
                      json={"journal": "felt strong"}, headers=auth)
    assert resp.json()["item_actuals"]["main"]["sets"] == 5


def test_today_carries_counts_and_actuals(client, db, user, auth):
    """The Today tab is where sets get logged, so it needs both."""
    from datetime import date as _date
    b = DailyBrief(user_id=user.id, day=_date.today(), readiness_summary="s",
                   training_prescription="p", model="test", session_planned=True,
                   session_items=COUNTED)
    db.add(b)
    db.commit()
    client.put(f"/v1/insights/day/{_date.today()}/log",
               json={"item_actuals": {"main": {"sets": 4}}}, headers=auth)
    body = client.get("/v1/dashboard/today", headers=auth).json()
    assert body["session_items"][0]["sets"] == 3
    assert body["item_actuals"]["main"]["sets"] == 4
