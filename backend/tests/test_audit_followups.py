"""Defects found reviewing the launch-safety audit branch.

Each of these was introduced or left standing by that pass, and each is the
kind that only shows up against real accounts: an athlete who predates a
column, a phone that spells UUIDs in upper case, a notification type Apple
added after the library shipped.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.consent import (
    CURRENT_LEGAL_VERSION,
    accepted_current_documents,
    age_eligible,
    has_consent,
)
from app.models import DailyBrief, DayLog, LegalConsent, User


def _consented(db, *, dob=date(1990, 1, 1)):
    u = User(email=f"{uuid.uuid4().hex}@example.com", date_of_birth=dob)
    db.add(u)
    db.flush()
    db.add_all([
        LegalConsent(user_id=u.id, document=d, version=CURRENT_LEGAL_VERSION)
        for d in ("terms", "privacy")
    ])
    db.commit()
    return u


class TestTheGateTheAppReadsMatchesTheOneWeEnforce:
    """Two production accounts had accepted both documents and had no birth
    date. `up_to_date` looked only at the terms version, so the consent screen
    passed them through and every other endpoint then refused them — with
    nothing in the app to tap."""

    def test_a_missing_birth_date_is_not_agreement(self, db):
        u = _consented(db, dob=None)
        assert accepted_current_documents(db, u) is True
        assert age_eligible(u) is False
        assert has_consent(db, u) is False

    def test_the_app_is_told_to_re_prompt(self, client, db):
        from app.auth.jwt import issue_jwt

        u = _consented(db, dob=None)
        auth = {"Authorization": f"Bearer {issue_jwt(u.id)}"}
        status = client.get("/v1/me/consent", headers=auth).json()
        assert status["up_to_date"] is False, "otherwise the consent screen never shows"

    def test_the_two_refusals_read_differently(self, client, db):
        from app.auth.jwt import issue_jwt

        no_dob = _consented(db, dob=None)
        r = client.post(
            "/v1/ingest/healthkit",
            headers={"Authorization": f"Bearer {issue_jwt(no_dob.id)}"},
            json={"workouts": []},
        )
        assert r.status_code == 403
        assert "date of birth" in r.json()["detail"]

    def test_supplying_the_date_reopens_the_account(self, client, db):
        from app.auth.jwt import issue_jwt

        u = _consented(db, dob=None)
        auth = {"Authorization": f"Bearer {issue_jwt(u.id)}"}
        r = client.post("/v1/me/consent", headers=auth, json={
            "version": CURRENT_LEGAL_VERSION, "date_of_birth": "1990-01-01",
        })
        assert r.status_code == 201
        assert r.json()["up_to_date"] is True
        db.expire_all()
        assert has_consent(db, db.get(User, u.id)) is True


class TestAppleAccountTokens:
    def test_an_upper_case_token_is_the_same_account(self):
        """Swift renders a UUID upper case, Python lower. A string compare
        rejects a purchase the athlete made on their own phone."""
        from app.routes.subscriptions import _same_account

        uid = uuid.uuid4()
        assert _same_account(str(uid).upper(), uid) is True
        assert _same_account(str(uid), uid) is True

    def test_a_different_account_still_fails(self):
        from app.routes.subscriptions import _same_account

        assert _same_account(str(uuid.uuid4()), uuid.uuid4()) is False

    def test_nothing_is_not_a_match(self):
        from app.routes.subscriptions import _same_account

        uid = uuid.uuid4()
        assert _same_account(None, uid) is False
        assert _same_account("", uid) is False
        assert _same_account("not-a-uuid", uid) is False


class TestNotificationTypesSurvive:
    def test_a_known_type_uses_its_wire_value(self):
        from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2

        from app.subscriptions.apple import _enum_or_raw

        payload = type("P", (), {
            "notificationType": NotificationTypeV2.REFUND, "rawNotificationType": "REFUND",
        })
        # str(enum) is "NotificationTypeV2.REFUND", which matched nothing.
        assert _enum_or_raw(payload, "notificationType") == "REFUND"

    def test_a_type_the_library_predates_is_not_lost(self):
        from app.subscriptions.apple import _enum_or_raw

        payload = type("P", (), {
            "notificationType": None, "rawNotificationType": "SOME_FUTURE_REVOKE",
        })
        assert _enum_or_raw(payload, "notificationType") == "SOME_FUTURE_REVOKE"


class TestOvernightRewriteRespectsAStartedSession:
    """Late sleep should still replace a plan written without it — that is the
    entire feature — but not once the athlete has begun the session."""

    def _brief(self, db, user, **over):
        b = DailyBrief(
            user_id=user.id, day=over.pop("day"), model="m",
            readiness_summary="x", training_prescription="y", session_planned=True,
            session_items=[{"id": "squat", "text": "Back squat, 4x5"}],
            had_overnight_sleep=False, **over,
        )
        db.add(b)
        db.commit()
        return b

    def test_an_untouched_plan_is_rewritten(self, db):
        from app.dates import today_for
        from app.jobs.daily_brief import _session_untouched

        u = _consented(db)
        b = self._brief(db, u, day=today_for(u))
        assert _session_untouched(db, u, today_for(u), b) is True

    def test_a_ticked_item_protects_the_plan(self, db):
        from app.dates import today_for
        from app.jobs.daily_brief import _session_untouched

        u = _consented(db)
        day = today_for(u)
        b = self._brief(db, u, day=day)
        db.add(DayLog(user_id=u.id, day=day, completed_items=["squat"]))
        db.commit()
        assert _session_untouched(db, u, day, b) is False

    def test_a_logged_weight_protects_the_plan(self, db):
        from app.dates import today_for
        from app.jobs.daily_brief import _session_untouched

        u = _consented(db)
        day = today_for(u)
        b = self._brief(db, u, day=day)
        db.add(DayLog(user_id=u.id, day=day, item_actuals={"squat": {"weight": 100}}))
        db.commit()
        assert _session_untouched(db, u, day, b) is False

    def test_a_session_they_negotiated_protects_the_plan(self, db):
        from app.dates import today_for
        from app.jobs.daily_brief import _session_untouched

        u = _consented(db)
        day = today_for(u)
        b = self._brief(db, u, day=day, adjusted_at=datetime.now(timezone.utc))
        assert _session_untouched(db, u, day, b) is False
