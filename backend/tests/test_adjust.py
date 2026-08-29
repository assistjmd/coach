"""Negotiating today's session.

The prescription used to be take-it-or-leave-it, so an athlete with no barbell
either ignored the session or did it wrong — and both corrupt the compliance
signal that feeds back into tomorrow's brief.

The model is stubbed throughout. What is under test is everything around it: the
guardrails, the validation, and what happens to ticks when the session changes.
"""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.ai import adjust as adjust_mod
from app.ai.adjust import NoSessionToday, adjust_session
from app.dates import today_for
from app.models import ChatMessage, DailyBrief, DayLog, User


@pytest.fixture
def brief(db, user):
    b = DailyBrief(
        user_id=user.id,
        day=today_for(user),
        model="claude-sonnet-4-6",
        readiness_summary="Recovered.",
        training_prescription="Push day. Overhead press anchor.",
        session_planned=True,
        session_items=[
            {"id": "warmup", "text": "5 min easy walk"},
            {"id": "press", "text": "Barbell overhead press 3x8", "sets": 3, "reps": 8},
            {"id": "dips", "text": "Dips 3x10", "sets": 3, "reps": 10},
        ],
    )
    db.add(b)
    db.commit()
    return b


def _stub(monkeypatch, payload):
    """Make the Anthropic client return one canned JSON body."""
    class _Messages:
        def create(self, **kw):
            _Messages.last_kwargs = kw
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    class _Client:
        def __init__(self, **kw):
            self.messages = _Messages()

    monkeypatch.setattr(adjust_mod.anthropic, "Anthropic", _Client)
    return _Messages


def test_a_swap_replaces_the_session(db, user, brief, monkeypatch):
    _stub(monkeypatch, {
        "reply": "No barbell, no problem. Dumbbells for the press.",
        "changed": True,
        "training_prescription": "Push day. Dumbbell press anchor.",
        "session_items": [
            {"id": "warmup", "text": "5 min easy walk"},
            {"id": "press", "text": "Dumbbell overhead press 3x8", "sets": 3, "reps": 8},
            {"id": "dips", "text": "Dips 3x10", "sets": 3, "reps": 10},
        ],
    })

    result = adjust_session(db, user, "no barbell today")
    assert result.changed is True
    assert "Dumbbell" in result.brief.session_items[1]["text"]
    assert result.brief.training_prescription == "Push day. Dumbbell press anchor."
    assert result.brief.adjusted_at is not None
    # Their words, kept for tomorrow's brief.
    assert result.brief.adjustment_note == "no barbell today"


def test_the_current_session_is_given_to_the_model(db, user, brief, monkeypatch):
    """Including session_planned, which is what the rest-day guardrail hinges on."""
    msgs = _stub(monkeypatch, {"reply": "ok", "changed": False,
                               "training_prescription": brief.training_prescription,
                               "session_items": brief.session_items})
    adjust_session(db, user, "what's the point of dips")

    sent = msgs.last_kwargs["messages"][0]["content"]
    assert "session_planned" in sent
    assert "Barbell overhead press 3x8" in sent
    # Guardrails ride in the cached system block, not the user turn.
    systems = " ".join(b["text"] for b in msgs.last_kwargs["system"])
    assert "rest day, it stays a rest day" in systems


def test_a_question_leaves_the_session_alone(db, user, brief, monkeypatch):
    original = list(brief.session_items)
    _stub(monkeypatch, {
        "reply": "Dips build the lockout. Keep them.",
        "changed": False,
        "training_prescription": brief.training_prescription,
        "session_items": original,
    })

    result = adjust_session(db, user, "why dips?")
    assert result.changed is False
    assert result.brief.adjusted_at is None
    assert result.brief.session_items == original


def test_ticks_for_removed_exercises_are_dropped(db, user, brief, monkeypatch):
    """A tick surviving a swap counts completion toward something the athlete
    can no longer see, inflating compliance against a session never prescribed."""
    db.add(DayLog(
        user_id=user.id,
        day=brief.day,
        completed_items=["warmup", "dips"],
        item_actuals={"dips": {"sets": 4, "reps": 8}},
    ))
    db.commit()

    _stub(monkeypatch, {
        "reply": "Dropping dips, shoulder needs a break.",
        "changed": True,
        "training_prescription": "Push day, easier.",
        "session_items": [
            {"id": "warmup", "text": "5 min easy walk"},
            {"id": "press", "text": "Dumbbell press 3x8", "sets": 3, "reps": 8},
        ],
    })

    adjust_session(db, user, "shoulder is sore, skip dips")

    log = db.query(DayLog).filter(DayLog.user_id == user.id).one()
    assert log.completed_items == ["warmup"], "the dips tick must not survive"
    assert not log.item_actuals


def test_unusable_items_leave_the_original_intact(db, user, brief, monkeypatch):
    """The model claiming a change but returning junk must not wipe the session."""
    original = list(brief.session_items)
    _stub(monkeypatch, {
        "reply": "Changed it.",
        "changed": True,
        "training_prescription": "Something else.",
        "session_items": [{"text": "no id"}, {"id": "", "text": "empty id"}],
    })

    result = adjust_session(db, user, "change it")
    assert result.changed is False, "a claimed change with no valid items is not a change"
    assert result.brief.session_items == original
    assert result.brief.adjusted_at is None


def test_no_brief_today_is_refused(db, user, monkeypatch):
    _stub(monkeypatch, {"reply": "x", "changed": False})
    with pytest.raises(NoSessionToday):
        adjust_session(db, user, "make it easier")


def test_adjustments_reach_tomorrows_brief(db, user, brief, monkeypatch):
    """The request is the useful signal — without it the coach keeps prescribing
    equipment the athlete does not have."""
    from app.ai.data import gather_recent

    _stub(monkeypatch, {
        "reply": "Dumbbells it is.",
        "changed": True,
        "training_prescription": "Push day, dumbbells.",
        "session_items": [{"id": "press", "text": "DB press 3x8", "sets": 3, "reps": 8}],
    })
    adjust_session(db, user, "no barbell today")

    sessions = gather_recent(db, user, days=14)["recent_sessions"]
    assert sessions[0]["athlete_changed_this"] == "no barbell today"


def test_chat_questions_do_not_use_up_adjustments(client, auth, db, user, brief, monkeypatch):
    """The failure mode that made these separate: spending the day asking
    questions must not leave you unable to change your workout, which is the
    more valuable of the two."""
    from app.config import settings

    _stub(monkeypatch, {"reply": "ok", "changed": False,
                        "training_prescription": brief.training_prescription,
                        "session_items": brief.session_items})

    for _ in range(settings().chat_daily_message_limit + 5):
        db.add(ChatMessage(user_id=user.id, role="user", content="x", kind="chat"))
    db.commit()

    resp = client.post("/v1/dashboard/today/adjust", headers=auth,
                       json={"message": "make it easier"})
    assert resp.status_code == 200


def test_adjustments_are_metered_on_their_own_allowance(client, auth, db, user, brief, monkeypatch):
    """This runs on the expensive model. Unmetered it would be the cheapest way
    to run up the bill."""
    from app.config import settings

    _stub(monkeypatch, {"reply": "ok", "changed": False,
                        "training_prescription": brief.training_prescription,
                        "session_items": brief.session_items})

    for _ in range(settings().adjust_daily_limit):
        db.add(ChatMessage(user_id=user.id, role="user", content="x", kind="adjust"))
    db.commit()

    resp = client.post("/v1/dashboard/today/adjust", headers=auth,
                       json={"message": "make it easier"})
    assert resp.status_code == 429


def test_adjustments_do_not_use_up_chat_questions(client, auth, db, user, monkeypatch):
    from app.config import settings

    for _ in range(settings().adjust_daily_limit + 3):
        db.add(ChatMessage(user_id=user.id, role="user", content="x", kind="adjust"))
    db.commit()

    body = client.get("/v1/chat/quota", headers=auth).json()
    assert body["remaining"] == settings().chat_daily_message_limit
    assert body["adjust_remaining"] == 0


def test_adjustments_stay_out_of_the_chat_transcript(db, user, brief, monkeypatch):
    """Same table for metering and export, different conversation. Replaying an
    adjustment in chat would have the coach answering a question about a workout
    from two days ago."""
    _stub(monkeypatch, {"reply": "Dumbbells it is.", "changed": False,
                        "training_prescription": brief.training_prescription,
                        "session_items": brief.session_items})
    adjust_session(db, user, "no barbell")
    db.add(ChatMessage(user_id=user.id, role="user", content="how's my week?", kind="adjust"))
    db.commit()

    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user.id, ChatMessage.kind == "chat")
        .all()
    )
    assert rows == []


def test_an_empty_message_is_rejected(client, auth, brief):
    resp = client.post("/v1/dashboard/today/adjust", headers=auth, json={"message": "   "})
    assert resp.status_code == 400


def test_the_headline_helper_matches_the_apps_rules():
    """Mirror of DashboardView.headline, kept here because the rule it encodes —
    a short opener is a lead-in, not the whole point — is easy to break and has
    no Swift test target to guard it."""
    def headline(text: str) -> str:
        t = text.strip()
        idx = next((i for i, c in enumerate(t) if c in ".!?"), None)
        if idx is None:
            return t
        first = t[: idx + 1]
        if len(first) >= 24:
            return first
        rest = t[idx + 1 :].strip()
        second = next((i for i, c in enumerate(rest) if c in ".!?"), None)
        return first if second is None else first + " " + rest[: second + 1]

    # A short opener keeps the sentence that follows it.
    assert headline("Recovered. Train hard today. Sleep was good.") == (
        "Recovered. Train hard today."
    )
    # A full opening sentence stands alone.
    assert headline(
        "Second short night in a row and your HRV took a 12% hit. Readiness is 61."
    ) == "Second short night in a row and your HRV took a 12% hit."
    # No terminator at all is returned whole rather than truncated.
    assert headline("Easy day") == "Easy day"


class TestTheGuardrailsDoNotBlockNormalRequests:
    """Nine real requests produced one applied change. The model was not
    failing — it was obeying a rule that told it to refuse the most common ask
    there is, which was "add strength training to today"."""

    def test_adding_work_is_not_forbidden(self):
        from app.ai.prompts import ADJUST_PROMPT

        # The rule that caused it, gone.
        assert "belongs tomorrow" not in ADJUST_PROMPT
        assert "a fifth more" not in ADJUST_PROMPT
        # And replaced with the opposite default.
        assert "Start from the assumption that you are going to do it" in ADJUST_PROMPT
        assert "Adding work is a normal request" in ADJUST_PROMPT

    def test_telling_the_coach_what_you_did_is_handled(self):
        """"I already surfed" is news, not a request for a different session,
        and was being answered as though it were an argument to win."""
        from app.ai.prompts import ADJUST_PROMPT

        assert "WHEN THEY TELL YOU WHAT THEY DID" in ADJUST_PROMPT
        assert "Rebuild the day around it" in ADJUST_PROMPT

    def test_the_safety_rules_survived_the_rewrite(self):
        """Relaxing the volume rule must not have taken the real limits with
        it. A rest day is still a rest day, and pain still goes to a clinician."""
        from app.ai.prompts import ADJUST_PROMPT

        assert "it stays a rest day" in ADJUST_PROMPT
        assert "get it looked at" in ADJUST_PROMPT
        assert "readiness is genuinely poor" in ADJUST_PROMPT
