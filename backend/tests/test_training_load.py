"""Training load and compliance inference.

These are the two places in the insights feature where being confidently wrong
is worse than saying nothing: a load number the athlete can't trust makes the
whole calendar noise, and telling someone they skipped a session they did is the
fastest way to lose them.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.analytics.compliance import infer
from app.analytics.load import HRProfile, workout_load
from app.models import DailyBrief, Workout

HR = HRProfile(rest=50, max=190)


def workout(**kw) -> Workout:
    base = dict(
        started_at=datetime(2026, 8, 1, 6, tzinfo=timezone.utc),
        duration_s=3600,
        sport="running",
        avg_hr=None,
        max_hr=None,
    )
    return Workout(**{**base, **kw})


# --- load ---------------------------------------------------------------


def test_harder_effort_scores_higher_for_the_same_duration():
    """The whole point of TRIMP over duration: an hour is not just an hour."""
    easy, _ = workout_load(workout(avg_hr=120), HR)
    hard, _ = workout_load(workout(avg_hr=175), HR)
    assert hard > easy * 2


def test_heart_rate_is_reported_as_measured_not_estimated():
    _, method = workout_load(workout(avg_hr=150), HR)
    assert method == "trimp"


def test_missing_heart_rate_falls_back_and_says_so():
    load, method = workout_load(workout(avg_hr=None), HR)
    assert method == "duration"
    assert load > 0


def test_sport_differentiates_the_fallback():
    """Without HR the only signal left is what the activity was."""
    walk, _ = workout_load(workout(avg_hr=None, sport="walking"), HR)
    run, _ = workout_load(workout(avg_hr=None, sport="running"), HR)
    assert run > walk


def test_heart_rate_below_resting_does_not_go_negative():
    """A warm-up-only session can average under resting HR."""
    load, _ = workout_load(workout(avg_hr=40), HR)
    assert load >= 0


def test_heart_rate_above_observed_max_does_not_explode():
    """The exponential makes an unclamped ratio dangerous, not just wrong."""
    sane, _ = workout_load(workout(avg_hr=190), HR)
    spike, _ = workout_load(workout(avg_hr=250), HR)
    assert spike == pytest.approx(sane)


def test_zero_duration_is_zero_load():
    load, _ = workout_load(workout(duration_s=0, avg_hr=150), HR)
    assert load == 0.0


# --- compliance ---------------------------------------------------------


def brief(**kw) -> DailyBrief:
    base = dict(
        day=date(2026, 8, 1),
        readiness_summary="",
        training_prescription="",
        model="test",
        session_planned=True,
    )
    return DailyBrief(**{**base, **kw})


def test_session_asked_for_and_done():
    assert infer(brief(), day_load=80, typical_load=80) == "complete"


def test_session_asked_for_and_skipped():
    assert infer(brief(), day_load=0, typical_load=80) == "missed"


def test_token_effort_counts_as_partial():
    assert infer(brief(), day_load=20, typical_load=80) == "partial"


def test_rest_day_with_no_workout_is_not_a_miss():
    """Following a rest instruction is compliance, not failure."""
    assert infer(brief(session_planned=False), day_load=0, typical_load=80) == "rest"


def test_training_on_a_rest_day_is_not_punished():
    assert infer(brief(session_planned=False), day_load=90, typical_load=80) == "rest"


def test_no_brief_means_nothing_to_comply_with():
    assert infer(None, day_load=0, typical_load=80) == "unknown"


def test_briefs_predating_the_field_do_not_fabricate_misses():
    """Older rows have session_planned NULL. Marking them missed would invent a
    history of failure the athlete never had."""
    assert infer(brief(session_planned=None), day_load=0, typical_load=80) == "unknown"
    assert infer(brief(session_planned=None), day_load=90, typical_load=80) == "complete"


def test_no_baseline_yet_does_not_mark_everything_partial():
    """A brand new athlete has no typical load to compare against."""
    assert infer(brief(), day_load=10, typical_load=0) == "complete"
