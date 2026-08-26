"""Aggregate metrics, and the line they must not cross.

This module exists instead of reading users' conversations. The guarantee that
matters is not that the numbers are right — it is that no message body, journal
entry or prescription ever appears in the output.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.product import collect
from app.config import settings
from app.models import ChatMessage, DailyBrief, DayLog, User

NOW = datetime.now(timezone.utc)


@pytest.fixture
def admin(db, user, monkeypatch):
    user.email = "admin@example.com"
    db.commit()
    monkeypatch.setattr(settings(), "admin_emails", "admin@example.com", raising=False)
    return user


class TestNoContentEverLeaks:
    def test_message_bodies_are_absent(self, db, user):
        secret = "my shoulder has been hurting since the divorce"
        db.add(ChatMessage(user_id=user.id, role="user", content=secret, kind="chat"))
        db.add(DailyBrief(
            user_id=user.id, day=NOW.date(), model="m",
            readiness_summary="PRIVATE SUMMARY",
            training_prescription="PRIVATE PRESCRIPTION",
            session_planned=True,
        ))
        db.add(DayLog(user_id=user.id, day=NOW.date(), journal="PRIVATE JOURNAL"))
        db.commit()

        blob = str(collect(db).as_dict())
        for leaked in (secret, "PRIVATE SUMMARY", "PRIVATE PRESCRIPTION", "PRIVATE JOURNAL"):
            assert leaked not in blob

    def test_no_user_identifiers_appear(self, db, user):
        db.add(ChatMessage(user_id=user.id, role="user", content="hi", kind="chat"))
        db.commit()
        blob = str(collect(db).as_dict())
        assert str(user.id) not in blob
        assert (user.email or "nope") not in blob


class TestCounts:
    def test_chat_and_adjust_are_counted_apart(self, db, user):
        for _ in range(3):
            db.add(ChatMessage(user_id=user.id, role="user", content="x", kind="chat"))
        db.add(ChatMessage(user_id=user.id, role="user", content="x", kind="adjust"))
        # Assistant replies must not inflate the count of what people asked.
        db.add(ChatMessage(user_id=user.id, role="assistant", content="x", kind="chat"))
        db.commit()

        e = collect(db).engagement
        assert e["chat_messages_7d"] == 3
        assert e["adjust_requests_7d"] == 1
        assert e["users_who_chatted_7d"] == 1

    def test_stalled_onboarding_is_visible(self, db):
        """Signed in and never finished setup — the clearest sign of a broken
        first run, and invisible from anywhere else."""
        db.add(User(email="stalled@example.com", timezone="UTC"))
        db.add(User(email="done@example.com", timezone="UTC", onboarded_at=NOW))
        db.commit()

        u = collect(db).users
        assert u["stalled_in_onboarding"] >= 1

    def test_dormant_counts_only_accounts_old_enough_to_have_returned(self, db):
        db.add(User(email="fresh@example.com", timezone="UTC", created_at=NOW))
        db.commit()
        assert collect(db).retention["dormant_14d"] == 0

    def test_ratios_are_none_rather_than_a_division_by_zero(self, db):
        m = collect(db)
        assert m.retention["dau_over_wau_pct"] is None or isinstance(
            m.retention["dau_over_wau_pct"], float
        )
        assert m.engagement["adjust_declined_or_answered_pct"] is None

    def test_compliance_uses_the_same_resolution_as_the_app(self, db, user):
        """Resolved through the shared precedence chain, so this cannot disagree
        with what an athlete sees on their own screen."""
        items = [{"id": "a", "text": "Squat 3x5"}]
        b = DailyBrief(
            user_id=user.id, day=NOW.date(), model="m",
            readiness_summary="x", training_prescription="y",
            session_planned=True, compliance_auto="missed", session_items=items,
        )
        db.add(b)
        db.add(DayLog(user_id=user.id, day=NOW.date(), completed_items=["a"]))
        db.commit()

        # The tick outranks the inference, here as everywhere.
        assert collect(db).coaching["compliance_14d"].get("complete") == 1


class TestAccess:
    def test_a_normal_user_gets_a_404(self, client, auth):
        """404 rather than 403: an unauthorised caller learns nothing about
        whether the route exists."""
        assert client.get("/v1/admin/metrics", headers=auth).status_code == 404

    def test_an_empty_allowlist_closes_the_route(self, client, auth, db, user, monkeypatch):
        monkeypatch.setattr(settings(), "admin_emails", "", raising=False)
        user.email = "anyone@example.com"
        db.commit()
        assert client.get("/v1/admin/metrics", headers=auth).status_code == 404

    def test_an_allowlisted_admin_gets_metrics(self, client, auth, admin):
        r = client.get("/v1/admin/metrics", headers=auth)
        assert r.status_code == 200
        assert "retention" in r.json()

    def test_it_needs_authentication_at_all(self, client):
        assert client.get("/v1/admin/metrics").status_code in (401, 403)


class TestNewColumnsDoNotInventBadNews:
    """`last_seen_at` and `trial_ends_at` are newer than some accounts. A null
    in either means "not observed yet", and reporting those as churn or as
    lapsed trials would show a problem that does not exist."""

    def test_never_seen_is_not_counted_as_dormant(self, db):
        db.add(User(
            email="old@example.com", timezone="UTC",
            created_at=NOW - timedelta(days=60), last_seen_at=None,
        ))
        db.commit()
        r = collect(db).retention
        assert r["dormant_14d"] == 0
        assert r["never_seen_since_tracking_began"] >= 1

    def test_a_genuinely_dormant_account_is_counted(self, db):
        db.add(User(
            email="gone@example.com", timezone="UTC",
            created_at=NOW - timedelta(days=60),
            last_seen_at=NOW - timedelta(days=30),
        ))
        db.commit()
        assert collect(db).retention["dormant_14d"] >= 1

    def test_an_unstarted_trial_is_not_a_lapsed_one(self, db):
        db.add(User(email="untouched@example.com", timezone="UTC", trial_ends_at=None))
        db.commit()
        money = collect(db).money
        assert money["trial_not_started"] >= 1
        assert money["trial_lapsed_not_paying"] == 0
        # No denominator yet, so no conversion rate to report.
        assert money["trial_to_paid_pct"] is None
