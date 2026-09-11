"""A refresh may change what the coach says. Never what it asked for.

The bug: logging a workout at 9am regenerated the whole brief, which rewrites
session_items — so a shoulder-and-back day became a leg day underneath someone
half way through it, and the exercises they had already ticked disappeared with
it. Reported from a gym, mid-session.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.dates import today_for
from app.jobs.daily_brief import MAX_REVISIONS, refresh_after_activity
from app.models import DailyBrief

SESSION = [
    {"id": "warmup", "text": "5 min easy walk + band pull-aparts"},
    {"id": "row", "text": "Chest-supported DB row 4x10", "sets": 4, "reps": 10},
    {"id": "press", "text": "Landmine press 3x10 each side", "sets": 3, "reps": 10},
]


@pytest.fixture
def brief(db, user):
    b = DailyBrief(
        user_id=user.id, day=today_for(user), model="m",
        readiness_summary="Recovered. Shoulders and back today.",
        training_prescription="Push and pull day.",
        session_planned=True, session_items=SESSION, revision=0,
    )
    db.add(b)
    db.commit()
    return b


def _model(payload):
    class _Messages:
        def create(self, **kw):
            import json
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )
    return lambda **kw: SimpleNamespace(messages=_Messages())


def test_the_session_survives_a_refresh(db, user, brief, monkeypatch):
    """The whole point. Even if the model returns a session, it is ignored."""
    from app.ai import headline as mod

    monkeypatch.setattr(mod.anthropic, "Anthropic", _model({
        "readiness_summary": "Session's done. Good work.",
        "readiness_score": 71,
        # A model that volunteers a different plan must not be able to install it.
        "session_items": [{"id": "squat", "text": "Back squat 5x5"}],
        "training_prescription": "Leg day.",
    }))

    assert refresh_after_activity(db, user) is True
    db.refresh(brief)
    assert brief.readiness_summary == "Session's done. Good work."
    assert brief.readiness_score == 71
    # Untouched, all of it.
    assert brief.session_items == SESSION
    assert brief.training_prescription == "Push and pull day."
    assert brief.session_planned is True


def test_an_unusable_reply_leaves_everything_alone(db, user, brief, monkeypatch):
    from app.ai import headline as mod

    class _Messages:
        def create(self, **kw):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="sorry")])

    monkeypatch.setattr(
        mod.anthropic, "Anthropic",
        lambda **kw: SimpleNamespace(messages=_Messages()),
    )
    assert refresh_after_activity(db, user) is False
    db.refresh(brief)
    assert brief.readiness_summary == "Recovered. Shoulders and back today."
    assert brief.revision == 1  # Failed model attempts still spend the allowance.


def test_the_ceiling_actually_holds(db, user, brief, monkeypatch):
    """Today's brief reached revision 15 against a cap of 3, because the count
    was read from an instance that a previous request had already moved on."""
    from app.ai import headline as mod

    monkeypatch.setattr(mod.anthropic, "Anthropic", _model({
        "readiness_summary": "Updated.", "readiness_score": 60,
    }))

    for _ in range(10):
        refresh_after_activity(db, user)
    db.refresh(brief)
    assert brief.revision == MAX_REVISIONS


def test_a_stale_instance_cannot_reopen_the_ceiling(db, user, brief, monkeypatch):
    """A phone chunks its upload, so several ingests land in a row and each must
    see what the one before it wrote."""
    from app.ai import headline as mod

    monkeypatch.setattr(mod.anthropic, "Anthropic", _model({
        "readiness_summary": "Updated.", "readiness_score": 60,
    }))
    brief.revision = MAX_REVISIONS
    db.commit()
    # Hand the function a view of the row from before that write.
    db.expire(brief)
    assert refresh_after_activity(db, user) is False


def test_nothing_happens_without_a_brief(db, user):
    assert refresh_after_activity(db, user) is False
