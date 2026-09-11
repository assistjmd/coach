"""What actually reaches Claude.

These are cheap and specific: the goal block was being built by interpolating a
dict straight into an f-string, so the model received a Python repr instead of a
sentence. Nothing failed loudly — the coach just reasoned over noise.
"""

from app.ai.prompts import user_context_block


def profile(**goal) -> dict:
    return {"display_name": "Jake", "timezone": "America/Chicago", "goal": goal or None}


def test_goal_is_prose_not_a_dict_repr():
    block = user_context_block(
        profile(kind="race", sport="running", target_date="2026-10-01", description=None)
    )
    for artifact in ("{", "}", "'kind'", "None"):
        assert artifact not in block, f"dict repr leaked into the prompt: {block!r}"


def test_goal_mentions_sport_and_date():
    block = user_context_block(profile(kind="race", sport="running", target_date="2026-10-01"))
    assert "running" in block
    assert "2026-10-01" in block


def test_athlete_description_is_carried_through():
    block = user_context_block(profile(kind="improvement", description="want a sub-3 marathon"))
    assert "sub-3 marathon" in block


def test_params_are_rendered_readably():
    block = user_context_block(profile(kind="improvement", sport="cycling", params={"ftp_w": 280}))
    assert "ftp_w=280" in block
    assert "{" not in block


def test_no_goal_is_simply_omitted():
    block = user_context_block({"display_name": "Jake", "timezone": "UTC", "goal": None})
    assert "Goal" not in block
    assert "Jake" in block


def test_anonymous_user_has_a_placeholder():
    assert "(anonymous)" in user_context_block({"display_name": None})


class TestAbsentDataIsAbsent:
    """The coach should never open by naming what the athlete's setup lacks.

    `"hrv": []` in the context is an explicit statement that HRV is missing, and
    the model quite reasonably mentions it — which on the home screen becomes a
    daily complaint about equipment they do not own. Most people have sleep and
    workouts and no HRV.
    """

    def test_empty_sections_are_dropped(self, db, user):
        from app.ai.data import gather_recent

        snap = gather_recent(db, user, days=14)
        for key in ("hrv", "readiness", "workouts", "sleep", "body_metrics"):
            assert snap.get(key) != [], f"{key} is present but empty"

    def test_sections_with_data_survive(self, db, user):
        from datetime import datetime, timedelta, timezone

        from app.ai.data import gather_recent
        from app.models import Workout

        # Relative to now: `gather_recent` windows back from the real clock, so
        # a fixed date eventually falls outside it and the section goes missing
        # for a reason that has nothing to do with the behaviour under test.
        started = datetime.now(timezone.utc) - timedelta(days=3)
        db.add(Workout(
            user_id=user.id, source="healthkit", source_record_id="w1",
            started_at=started, ended_at=started + timedelta(hours=1),
            duration_s=3600, sport="running", avg_hr=150,
        ))
        db.commit()

        snap = gather_recent(db, user, days=14)
        assert len(snap["workouts"]) == 1
        assert "hrv" not in snap

    def test_the_goal_survives_being_unset(self, db, user):
        """A missing goal is actionable — the athlete can set one. A missing
        sensor is not."""
        from app.ai.data import gather_recent

        assert "goal" in gather_recent(db, user, days=14)

    def test_the_prompt_forbids_naming_missing_metrics(self):
        from app.ai.prompts import SYSTEM_PROMPT

        assert "Do not list what is missing" in SYSTEM_PROMPT
        assert "never ask them to connect something" in SYSTEM_PROMPT
        # The rule that must survive alongside it.
        assert "Never invent or estimate a number" in SYSTEM_PROMPT
