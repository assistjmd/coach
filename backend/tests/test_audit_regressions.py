"""Launch audit regressions. No Apple or model service is called."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import uuid

import pytest
from fastapi import HTTPException

from app.config import settings
from app.dates import today_for
from app.db import SessionLocal
from app.entitlement import evaluate
from app.models import BodyMetric, ChatMessage, DailyBrief, DayLog, SleepSession, User, Workout


def _brief(db, user):
    b = DailyBrief(user_id=user.id, day=today_for(user), model="test",
        readiness_summary="Original", training_prescription="Upper body",
        session_items=[{"id": "row", "text": "Row 3x8"}], revision=0,
        session_planned=True, had_overnight_sleep=False)
    db.add(b)
    db.commit()
    return b


def _model(payload):
    create = Mock(return_value=SimpleNamespace(content=[
        SimpleNamespace(type="text", text=json.dumps(payload))]))
    return SimpleNamespace(messages=SimpleNamespace(create=create)), create


@pytest.mark.parametrize("metric", ["dietary_energy_kcal", "protein_g", "carbs_g", "fat_g", "water_ml"])
def test_all_ios_nutrition_metrics_are_accepted(client, auth, metric):
    r = client.post("/v1/ingest/healthkit", headers=auth, json={"body_metrics": [
        {"metric": metric, "value": 30, "measured_at": "2026-09-10T00:00:00Z"}]})
    assert r.status_code == 200
    assert r.json()["body_metrics"] == 1


def test_daily_totals_update_without_duplicate_rows(client, auth, db, user):
    def send(value):
        return client.post("/v1/ingest/healthkit", headers=auth, json={"body_metrics": [
            {"metric": "steps", "value": value, "measured_at": "2026-09-10T00:00:00Z"}]})
    assert send(100).json()["body_metrics"] == 1
    assert send(8000).json()["body_metrics"] == 1
    assert send(8000).json()["body_metrics"] == 0
    assert db.query(BodyMetric).filter_by(user_id=user.id, metric="steps").one().value == 8000


@pytest.mark.parametrize("minutes,sport", [(30, "cycling"), (180, "running")])
def test_contained_or_incompatible_workout_is_preserved(db, user, minutes, sport):
    from app.routes.ingest import _ingest_workouts, HKWorkout
    start = datetime.now(timezone.utc) - timedelta(hours=4)
    rows = [HKWorkout(source_record_id="long-bike", sport="cycling", started_at=start,
                       ended_at=start + timedelta(hours=3), duration_s=10800),
            HKWorkout(source_record_id="real-session", sport=sport, started_at=start,
                       ended_at=start + timedelta(minutes=minutes), duration_s=minutes * 60)]
    assert _ingest_workouts(db, user, rows, "healthkit") == 2
    db.commit()
    assert db.query(Workout).filter_by(user_id=user.id).count() == 2


def test_full_sleep_snapshots_replace_partial_totals_and_preserve_devices(client, auth, db, user):
    start = datetime.now(timezone.utc) - timedelta(days=2)
    def send(device, hours):
        return client.post("/v1/ingest/healthkit", headers=auth, json={"sleep": [{
            "source_record_id": device + "-night", "source_device": device, "is_snapshot": True,
            "started_at": start.isoformat(), "ended_at": (start + timedelta(hours=hours)).isoformat(),
            "light_s": hours * 3600}]})
    assert send("watch", 2).status_code == 200
    assert send("watch", 3).status_code == 200
    assert send("ring", 3).status_code == 200
    assert send("watch", 3).status_code == 200
    rows = db.query(SleepSession).filter_by(user_id=user.id).all()
    assert len(rows) == 2
    assert {r.source_device for r in rows} == {"watch", "ring"}
    assert all(r.light_s == 10800 for r in rows)


def test_failed_headline_calls_exhaust_budget(db, user, monkeypatch):
    from app.ai import headline
    from app.jobs.daily_brief import MAX_REVISIONS, refresh_after_activity
    b = _brief(db, user)
    ai, create = _model({})
    monkeypatch.setattr(headline.anthropic, "Anthropic", lambda **kw: ai)
    for _ in range(7):
        assert refresh_after_activity(db, user) is False
    assert create.call_count == MAX_REVISIONS
    db.refresh(b)
    assert b.revision == MAX_REVISIONS


def test_failed_initial_briefs_are_bounded(db, user, monkeypatch):
    from app.ai import brief
    ai, create = _model({"readiness_summary": {"bad": "shape"}})
    monkeypatch.setattr(brief.anthropic, "Anthropic", lambda **kw: ai)
    for _ in range(7):
        with pytest.raises(brief.BriefUnavailable):
            brief.generate_daily_brief(db, user)
    assert create.call_count == 3
    assert db.query(DailyBrief).filter_by(user_id=user.id).count() == 0


def test_expired_account_can_ingest_without_spending_model_tokens(client, auth, db, user, monkeypatch):
    from app.ai import headline
    user.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()
    monkeypatch.setattr(settings(), "subscriptions_enforced", True)
    _brief(db, user)
    ai, create = _model({"readiness_summary": "Updated"})
    monkeypatch.setattr(headline.anthropic, "Anthropic", lambda **kw: ai)
    now = datetime.now(timezone.utc)
    payload = {"workouts": [{"source_record_id": "unpaid-workout", "duration_s": 1800,
        "started_at": (now - timedelta(minutes=30)).isoformat(), "ended_at": now.isoformat()}]}
    assert client.post("/v1/ingest/healthkit", headers=auth, json=payload).status_code == 200
    assert create.call_count == 0
    assert evaluate(db, user).state == "expired"


def test_scheduler_skips_expired_account(db, user, monkeypatch):
    from app.jobs import daily_brief as jobs
    user.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()
    monkeypatch.setattr(settings(), "subscriptions_enforced", True)
    monkeypatch.setattr(jobs, "SessionLocal", lambda: nullcontext(db))
    with patch.object(jobs, "generate_daily_brief") as generate:
        jobs.run_for_all_users(datetime.now(timezone.utc).replace(hour=11))
        generate.assert_not_called()


def test_late_sleep_keeps_started_plan_and_weights(db, user, monkeypatch):
    from app.jobs import daily_brief as jobs
    from app.ai import headline
    b = _brief(db, user)
    db.add(DayLog(user_id=user.id, day=b.day, completed_items=["row"], item_actuals={"row": {"weight": 40}}))
    db.commit()
    ai, _ = _model({"readiness_summary": "More rested", "readiness_score": 80,
                    "session_items": [{"id": "squat", "text": "Squat 5x5"}]})
    monkeypatch.setattr(headline.anthropic, "Anthropic", lambda **kw: ai)
    monkeypatch.setattr(jobs, "last_night_arrived", lambda *a: True)
    assert jobs.regenerate_if_sleep_landed(db, user)
    assert b.session_items[0]["id"] == "row"
    assert b.training_prescription == "Upper body"
    assert b.readiness_summary == "More rested"
    log = db.query(DayLog).filter_by(user_id=user.id).one()
    assert log.completed_items == ["row"]
    assert log.item_actuals == {"row": {"weight": 40}}
    assert not jobs.regenerate_if_sleep_landed(db, user)


def _tx(user, *, original="audit-transaction", env="Production", expires=None, signed=None, transaction="renewal-2", revoked=None):
    from app.subscriptions.apple import AppleTransaction
    now = datetime.now(timezone.utc)
    return AppleTransaction(original, "com.trainwithcoach.app.monthly", env,
        expires or now + timedelta(days=20), revoked, True, str(user.id), signed or now, transaction)


def test_receipt_is_bound_to_account(db, user):
    from app.routes.subscriptions import _upsert
    other = User(email=f"{uuid.uuid4()}@example.test")
    db.add(other)
    db.commit()
    tx = _tx(user)
    with pytest.raises(HTTPException) as rejected:
        _upsert(db, other.id, tx)
    assert rejected.value.status_code == 409
    db.rollback()
    _upsert(db, user.id, tx)
    with pytest.raises(HTTPException):
        _upsert(db, other.id, tx)
    db.rollback()
    assert evaluate(db, user).state == "subscribed"
    assert evaluate(db, other).state == "trial"


def test_sandbox_requires_explicit_production_test_account(db, user, monkeypatch):
    from app.routes.subscriptions import _upsert
    monkeypatch.setattr(settings(), "app_env", "prod")
    tx = _tx(user, env="Sandbox")
    with pytest.raises(HTTPException):
        _upsert(db, user.id, tx)
    monkeypatch.setattr(settings(), "sandbox_subscription_user_ids", str(user.id))
    _upsert(db, user.id, tx)
    assert evaluate(db, user).state == "subscribed"
    monkeypatch.setattr(settings(), "sandbox_subscription_user_ids", "")
    assert evaluate(db, user).state == "trial"


def test_old_receipts_and_refunds_do_not_regress_a_newer_renewal(db, user):
    from app.routes.subscriptions import _upsert
    now = datetime.now(timezone.utc)
    user.trial_ends_at = now - timedelta(days=30)
    db.commit()
    _upsert(db, user.id, _tx(user, signed=now))
    _upsert(db, user.id, _tx(user, expires=now - timedelta(days=1), signed=now + timedelta(seconds=1), transaction="renewal-1"))
    assert evaluate(db, user).state == "subscribed"
    _upsert(db, user.id, _tx(user, signed=now + timedelta(seconds=2), revoked=now))
    assert evaluate(db, user).state == "expired"
    _upsert(db, user.id, _tx(user, signed=now))
    assert evaluate(db, user).state == "expired"
    _upsert(db, user.id, _tx(user, expires=now + timedelta(days=50), signed=now + timedelta(seconds=3), transaction="renewal-3"))
    assert evaluate(db, user).state == "subscribed"


def test_concurrent_quota_reservations_allow_only_one_remaining_message():
    from app.quota import enforce, used_recently
    with SessionLocal() as db:
        u = User(email=f"{uuid.uuid4()}@example.test")
        db.add(u)
        db.commit()
        uid = u.id
        db.add_all([ChatMessage(user_id=uid, role="user", content="test", kind="chat") for _ in range(24)])
        db.commit()
    barrier = Barrier(2)
    def request(_):
        with SessionLocal() as db:
            u = db.get(User, uid)
            barrier.wait(timeout=10)
            try:
                enforce(db, u, "chat")
                db.add(ChatMessage(user_id=uid, role="user", content="race", kind="chat"))
                db.commit()
                return 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(request, range(2))) == [200, 429]
        with SessionLocal() as db:
            assert used_recently(db, db.get(User, uid), "chat") == 25
    finally:
        with SessionLocal() as db:
            db.delete(db.get(User, uid))
            db.commit()


def test_unconsented_account_cannot_upload_or_generate(client, unconsented_user):
    from app.auth.jwt import issue_jwt
    auth = {"Authorization": f"Bearer {issue_jwt(unconsented_user.id)}"}
    assert client.post("/v1/ingest/healthkit", headers=auth, json={}).status_code == 403
    assert client.post("/v1/chat", headers=auth, json={"message": "hi"}).status_code == 403
    assert client.get("/v1/me/export", headers=auth).status_code == 200


def test_sse_round_trips_newlines(client, auth, db, monkeypatch):
    from app.routes import chat
    message = "First line\nSecond line\n\n• Third"
    monkeypatch.setattr(chat, "stream_chat", lambda *a: iter([message]))
    monkeypatch.setattr(chat, "SessionLocal", lambda: nullcontext(db))
    r = client.post("/v1/chat", headers={**auth, "X-Coach-Stream-Version": "2"}, json={"message": "hello"})
    assert r.status_code == 200
    payload = next(line[6:] for line in r.text.splitlines() if line.startswith("data: "))
    assert json.loads(payload)["delta"] == message


def test_corrected_sleep_snapshot_can_move_its_window(client, auth, db, user):
    for day in (8, 9):
        response = client.post("/v1/ingest/healthkit", headers=auth, json={"sleep": [{
            "source_record_id": "corrected-night", "source_device": "watch", "is_snapshot": True,
            "started_at": f"2026-09-{day:02}T01:00:00Z",
            "ended_at": f"2026-09-{day:02}T07:00:00Z", "light_s": 21600}]})
        assert response.status_code == 200
    row = db.query(SleepSession).filter_by(user_id=user.id).one()
    assert row.started_at == datetime(2026, 9, 9, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_model_readiness_rejects_nonfinite_values(bad):
    from pydantic import ValidationError
    from app.ai.validation import BriefPayload, HeadlinePayload
    with pytest.raises(ValidationError):
        BriefPayload(readiness_summary="Ready", training_prescription="Rest", readiness_score=bad)
    with pytest.raises(ValidationError):
        HeadlinePayload(readiness_score=bad)


def test_model_cannot_turn_string_false_into_a_plan_change():
    from pydantic import ValidationError
    from app.ai.validation import AdjustPayload
    with pytest.raises(ValidationError):
        AdjustPayload(reply="No change", changed="false")
