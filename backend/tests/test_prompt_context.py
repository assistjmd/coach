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
