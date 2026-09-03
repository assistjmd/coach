"""Who gets to use the parts that cost money.

The rule that matters most here is what stays open when a subscription lapses.
Generating new coaching costs tokens and is gated; reading history the athlete
already created is not, and the export endpoint must keep working regardless or
the erasure and portability promises in the privacy policy stop being true.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.entitlement import ensure_trial, evaluate
from app.models import Subscription, User

NOW = datetime.now(timezone.utc)


@pytest.fixture
def enforced(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "subscriptions_enforced", True, raising=False)
    yield


def _sub(db, user, **over):
    s = Subscription(
        user_id=user.id,
        original_transaction_id=over.get("tx", "1000000000000001"),
        product_id="com.trainwithcoach.app.monthly",
        environment="Production",
        expires_at=over.get("expires", NOW + timedelta(days=20)),
        revoked=over.get("revoked", False),
    )
    db.add(s)
    db.commit()
    return s


def test_a_new_account_starts_a_trial(db, user):
    assert user.trial_ends_at is None
    e = evaluate(db, user)
    assert e.state == "trial"
    assert e.trial_days_left in (settings().trial_days, settings().trial_days - 1)


def test_the_trial_is_not_extended_by_looking_at_it(db, user):
    ensure_trial(db, user)
    first = user.trial_ends_at
    ensure_trial(db, user)
    assert user.trial_ends_at == first


def test_an_expired_trial_with_no_subscription_is_locked_out(db, user):
    user.trial_ends_at = NOW - timedelta(days=1)
    db.commit()
    e = evaluate(db, user)
    assert e.state == "expired"
    assert e.allowed is False


def test_a_live_subscription_outranks_an_expired_trial(db, user):
    user.trial_ends_at = NOW - timedelta(days=30)
    db.commit()
    _sub(db, user)
    e = evaluate(db, user)
    assert e.state == "subscribed"
    assert e.allowed is True


def test_a_refund_ends_access_immediately(db, user):
    """Revocation is separate from expiry because a refund arrives while the
    period is still notionally paid for."""
    user.trial_ends_at = NOW - timedelta(days=30)
    db.commit()
    _sub(db, user, revoked=True, expires=NOW + timedelta(days=20))
    assert evaluate(db, user).state == "expired"


def test_a_lapsed_subscription_is_expired(db, user):
    user.trial_ends_at = NOW - timedelta(days=60)
    db.commit()
    _sub(db, user, expires=NOW - timedelta(days=2))
    assert evaluate(db, user).state == "expired"


class TestGating:
    def test_paid_endpoints_are_open_while_enforcement_is_off(self, user, db):
        """Off until the products exist in App Store Connect, so a deploy cannot
        lock out every current tester."""
        from app.entitlement import require

        user.trial_ends_at = NOW - timedelta(days=30)
        db.commit()
        # Returns rather than raising: no paywall while enforcement is off.
        assert require(user=user, db=db) is user

    def test_an_expired_account_is_paywalled(self, client, auth, user, db, enforced):
        user.trial_ends_at = NOW - timedelta(days=30)
        db.commit()
        r = client.post("/v1/chat", headers=auth, json={"message": "hi"})
        assert r.status_code == 402

    def test_history_stays_readable_after_the_trial_ends(self, client, auth, user, db, enforced):
        """Taking away data the athlete created is a hostage tactic."""
        user.trial_ends_at = NOW - timedelta(days=30)
        db.commit()
        for path in ("/v1/me", "/v1/insights/calendar?start=2026-08-01&end=2026-08-25"):
            assert client.get(path, headers=auth).status_code == 200

    def test_export_survives_expiry(self, client, auth, user, db, enforced):
        """The privacy policy promises a copy on request. A lapsed subscription
        does not suspend that."""
        user.trial_ends_at = NOW - timedelta(days=30)
        db.commit()
        assert client.get("/v1/me/export", headers=auth).status_code == 200

    def test_account_deletion_survives_expiry(self, client, auth, user, db, enforced):
        user.trial_ends_at = NOW - timedelta(days=30)
        db.commit()
        assert client.delete("/v1/me", headers=auth).status_code == 204


class TestStatusEndpoint:
    def test_status_reports_the_trial(self, client, auth):
        body = client.get("/v1/subscriptions/status", headers=auth).json()
        assert body["state"] == "trial"
        assert body["trial_days_left"] > 0
        # The client must know not to show a paywall it cannot satisfy.
        assert body["enforced"] is False

    def test_an_unverifiable_transaction_is_refused(self, client, auth):
        r = client.post(
            "/v1/subscriptions/verify",
            headers=auth,
            json={"signed_transaction": "not.a.real.jws"},
        )
        assert r.status_code == 400

    def test_a_notification_without_a_payload_is_not_retried(self, client):
        """Apple retries non-2xx for days, and retrying will not make a
        malformed payload well-formed."""
        r = client.post("/v1/subscriptions/apple/notifications", json={})
        assert r.status_code == 200
        assert r.json()["ok"] is False


class TestAppleVerification:
    def test_forged_transactions_are_rejected(self):
        """A decoded-but-unverified payload is attacker-controlled JSON."""
        import base64
        import json

        from app.subscriptions.apple import VerificationError, verify_transaction

        def part(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        forged = ".".join([
            part({"alg": "ES256", "x5c": []}),
            part({
                "originalTransactionId": "1",
                "productId": "com.trainwithcoach.app.yearly",
                "expiresDate": 99999999999999,
            }),
            "signature",
        ])
        with pytest.raises(VerificationError):
            verify_transaction(forged)

    def test_apple_root_certificates_are_present(self):
        """Without them nothing can be verified, and the failure has to be loud
        rather than a silent fallback to trusting the payload."""
        from app.subscriptions.apple import _roots

        roots = _roots()
        assert len(roots) >= 3


class TestTheTrialEndsWithWarning:
    """Enforcement makes the difference between a trial ending and the app
    appearing to break. The client needs enough to warn with."""

    def test_days_left_counts_down_truthfully(self, db, user):
        user.trial_ends_at = NOW + timedelta(days=2, hours=3)
        db.commit()
        e = evaluate(db, user)
        assert e.state == "trial"
        # Ceiling, so a trial with hours left never reads as zero days while
        # access is genuinely still open.
        assert e.trial_days_left == 3

    def test_the_last_day_still_reads_as_a_day(self, db, user):
        user.trial_ends_at = NOW + timedelta(hours=5)
        db.commit()
        assert evaluate(db, user).trial_days_left == 1

    def test_the_status_endpoint_carries_what_the_banner_needs(self, client, auth, db, user):
        user.trial_ends_at = NOW + timedelta(days=1, hours=2)
        db.commit()
        body = client.get("/v1/subscriptions/status", headers=auth).json()
        assert body["state"] == "trial"
        assert body["trial_days_left"] == 2
        # Without this the client cannot tell a real deadline from a dry run.
        assert "enforced" in body

    def test_a_subscriber_is_never_warned(self, db, user):
        user.trial_ends_at = NOW + timedelta(hours=1)
        db.commit()
        _sub(db, user)
        e = evaluate(db, user)
        assert e.state == "subscribed"
        assert e.trial_days_left == 0, "no countdown for someone who already paid"
