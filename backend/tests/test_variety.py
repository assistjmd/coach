"""Counting what the coach has prescribed, because listing it did not work.

Fourteen days, fourteen near-identical easy runs, with ten of them visible in
the context the whole time. A list of past sessions is something a model reads
past. A count is not.
"""

from datetime import timedelta

from app.analytics.variety import _headline, classify, summarise
from app.dates import today_for
from app.models import DailyBrief, User


def _user(db):
    u = User(email=f"v{id(db)}@example.com", timezone="UTC")
    db.add(u)
    db.flush()
    return u


def _brief(db, user, days_ago, items):
    db.add(DailyBrief(
        user_id=user.id, day=today_for(user) - timedelta(days=days_ago),
        model="m", readiness_summary="x", training_prescription="y",
        session_planned=True,
        session_items=[{"id": f"i{n}", "text": t} for n, t in enumerate(items)],
    ))
    db.flush()


class TestHeadline:
    def test_the_prescription_is_stripped_from_the_exercise(self):
        assert _headline("Back squat, 4x5 — controlled descent") == "back squat"
        assert _headline("Dumbbell single-arm row, 3x10 each side") == "dumbbell single-arm row"

    def test_a_duration_range_does_not_become_the_exercise(self):
        """"35–40 min easy run" was reducing to "35", which counted every run as
        a different movement and reported variety that was not there."""
        assert _headline("35–40 min easy run, HR under 140") == "easy run"
        assert _headline("15 min easy run, HR hard cap at 140") == "easy run"


class TestClassification:
    def test_patterns_are_recognised(self):
        assert classify("Barbell overhead press 3x8") == "vertical push"
        assert classify("Chin-up, 4x5") == "vertical pull"
        assert classify("Romanian deadlift 3x8") == "hinge"
        assert classify("6x400m intervals") == "hard run"

    def test_unknown_movements_are_not_forced_into_a_pattern(self):
        assert classify("Underwater basket weaving") is None


class TestSummary:
    def test_the_repeated_thing_is_named_with_its_count(self, db):
        """The headline number. Fourteen identical days should be impossible to
        miss in the context."""
        user = _user(db)
        for i in range(1, 15):
            _brief(db, user, i, ["5 min easy walk", "35 min easy run", "5 min walk"])
        db.commit()

        v = summarise(db, user, days=14)
        assert v.most_repeated == "easy run"
        assert v.most_repeated_count >= 13

    def test_warmups_are_not_counted_as_repetition(self, db):
        """A five minute walk before every session is not the repetition anyone
        is complaining about."""
        user = _user(db)
        for i in range(1, 6):
            _brief(db, user, i, ["5 min easy walk", "Back squat 3x5"])
        db.commit()

        v = summarise(db, user, days=14)
        assert v.most_repeated == "back squat"

    def test_untouched_patterns_are_reported(self, db):
        """Knowing what has been neglected turns "vary it" into a choice between
        named alternatives."""
        user = _user(db)
        for i in range(1, 6):
            _brief(db, user, i, ["Back squat 3x5"])
        db.commit()

        v = summarise(db, user, days=14)
        assert "squat" not in v.untouched_patterns
        for expected in ("hinge", "vertical pull", "carry"):
            assert expected in v.untouched_patterns

    def test_days_since_reflects_the_most_recent_use(self, db):
        user = _user(db)
        _brief(db, user, 1, ["Deadlift 3x5"])
        _brief(db, user, 9, ["Back squat 3x5"])
        db.commit()

        v = summarise(db, user, days=14)
        assert v.days_since_pattern["hinge"] == 1
        assert v.days_since_pattern["squat"] == 9

    def test_no_history_is_not_an_error(self, db):
        v = summarise(db, _user(db), days=14)
        assert v.days_examined == 0
        assert v.most_repeated is None

    def test_it_reaches_the_model(self, db):
        from app.ai.data import gather_recent

        user = _user(db)
        for i in range(1, 15):
            _brief(db, user, i, ["35 min easy run"])
        db.commit()

        snap = gather_recent(db, user, days=14)
        assert snap["variety_check"]["most_repeated"] == "easy run"
        assert snap["variety_check"]["most_repeated_count"] >= 13


class TestAthleteMemory:
    def test_what_they_said_in_chat_reaches_the_brief(self, db, user):
        """Being told something once and then coached as though it was never
        said is the fastest way to make this feel like software."""
        from app.ai.data import gather_recent
        from app.models import ChatMessage

        db.add(ChatMessage(
            user_id=user.id, role="user", kind="chat",
            content="my knee has been sore since Saturday",
        ))
        db.add(ChatMessage(
            user_id=user.id, role="assistant", kind="chat", content="noted",
        ))
        db.commit()

        said = gather_recent(db, user, days=14)["athlete_said_recently"]
        assert [m["said"] for m in said] == ["my knee has been sore since Saturday"]

    def test_the_coachs_own_replies_are_not_fed_back(self, db, user):
        """They are already reflected in what it prescribed; replaying them
        doubles the context to restate its own decisions."""
        from app.ai.data import gather_recent
        from app.models import ChatMessage

        db.add(ChatMessage(
            user_id=user.id, role="assistant", kind="chat", content="ASSISTANT TEXT",
        ))
        db.commit()
        assert "ASSISTANT TEXT" not in str(gather_recent(db, user, days=14))
