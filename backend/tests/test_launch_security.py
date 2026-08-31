"""Attack surface at public launch.

Five trusted testers and an open App Store listing are different threat models.
Everything here is written from the attacker's side: what can an anonymous
stranger reach, what can one account do to another, and what can be made
expensive.
"""

import base64
import json
import uuid

import pytest

from app.models import ChatMessage, DailyBrief, DayLog, Goal, Subscription, User


@pytest.fixture
def victim(db):
    """A second account, with something worth stealing in it."""
    v = User(email="victim@example.com", timezone="UTC", apple_sub="victim-sub")
    db.add(v)
    db.flush()
    db.add(Goal(user_id=v.id, kind="race", description="VICTIM SECRET GOAL"))
    db.add(ChatMessage(user_id=v.id, role="user", kind="chat", content="VICTIM MESSAGE"))
    db.add(DailyBrief(
        user_id=v.id, day="2026-08-30", model="m",
        readiness_summary="VICTIM SUMMARY", training_prescription="VICTIM SESSION",
        session_planned=True,
    ))
    db.add(DayLog(user_id=v.id, day="2026-08-30", journal="VICTIM JOURNAL"))
    db.commit()
    return v


class TestNoCrossAccountAccess:
    """No route takes a user id from the caller, so this is structural rather
    than a rule someone has to remember. These prove it stays that way."""

    def test_another_accounts_data_never_appears(self, client, auth, victim):
        for path in (
            "/v1/me",
            "/v1/me/export",
            "/v1/goal",
            "/v1/chat/history",
            "/v1/insights/calendar?start=2026-08-01&end=2026-08-31",
            "/v1/subscriptions/status",
        ):
            body = client.get(path, headers=auth).text
            for secret in ("VICTIM SECRET GOAL", "VICTIM MESSAGE", "VICTIM SUMMARY",
                           "VICTIM JOURNAL", "victim@example.com"):
                assert secret not in body, f"{secret} leaked via {path}"

    def test_a_day_belonging_to_someone_else_cannot_be_edited(self, client, auth, db, victim):
        r = client.patch("/v1/insights/day/2026-08-30", headers=auth,
                         json={"compliance": "missed"})
        # Either no such day for this caller, or nothing of the victim's moved.
        assert r.status_code in (404, 200)
        db.refresh(victim)
        brief = db.query(DailyBrief).filter(DailyBrief.user_id == victim.id).one()
        assert brief.compliance_override is None

    def test_deleting_an_account_does_not_touch_another(self, client, auth, db, victim):
        assert client.delete("/v1/me", headers=auth).status_code == 204
        assert db.query(User).filter(User.id == victim.id).one_or_none() is not None


class TestTokens:
    def test_no_token_is_refused_everywhere(self, client):
        for method, path in (
            ("get", "/v1/me"), ("get", "/v1/me/export"), ("get", "/v1/chat/history"),
            ("get", "/v1/admin/metrics"), ("get", "/v1/subscriptions/status"),
            ("post", "/v1/dashboard/today/adjust"), ("get", "/v1/dashboard/today"),
        ):
            r = (client.post(path, json={}) if method == "post" else client.get(path))
            assert r.status_code in (401, 403, 404, 422), f"{path} answered {r.status_code}"

    def test_alg_none_is_refused(self, client):
        """The classic. The decoder pins the algorithm, so this must never work."""
        def part(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
        forged = part({"alg": "none"}) + "." + part(
            {"sub": str(uuid.uuid4()), "aud": "session", "iss": "coach"}
        ) + "."
        assert client.get("/v1/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401

    def test_a_token_for_a_deleted_account_stops_working(self, client, auth):
        """Deletion has to end the session, not merely remove the rows."""
        assert client.delete("/v1/me", headers=auth).status_code == 204
        assert client.get("/v1/me", headers=auth).status_code == 401

    def test_an_oauth_state_token_is_not_a_session(self, client, user):
        """It travels through a provider's logs and browser history. Accepting
        one as a bearer credential would be privilege escalation."""
        from app.auth.jwt import issue_state_token

        token, _ = issue_state_token(user.id, "oura")
        assert client.get("/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


class TestSubscriptionsCannotBeForged:
    """The whole revenue model rests on this being unfakeable from a phone."""

    def test_an_invented_transaction_is_rejected(self, client, auth):
        def part(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
        forged = ".".join([
            part({"alg": "ES256", "x5c": []}),
            part({
                "originalTransactionId": "9999",
                "productId": "com.trainwithcoach.app.yearly",
                "expiresDate": 99999999999999,
                "environment": "Production",
            }),
            "not-a-signature",
        ])
        r = client.post("/v1/subscriptions/verify", headers=auth,
                        json={"signed_transaction": forged})
        assert r.status_code == 400
        assert "could not be verified" in r.json()["detail"]

    def test_no_subscription_row_is_created_by_a_forgery(self, client, auth, db, user):
        client.post("/v1/subscriptions/verify", headers=auth,
                    json={"signed_transaction": "a.b.c"})
        assert db.query(Subscription).filter(Subscription.user_id == user.id).count() == 0

    def test_the_notification_webhook_rejects_unsigned_payloads(self, client):
        """Unauthenticated by necessity — Apple presents no token — so the
        signature is the entire authentication."""
        r = client.post("/v1/subscriptions/apple/notifications",
                        json={"signedPayload": "forged.payload.here"})
        assert r.status_code == 400

    def test_a_malformed_notification_is_not_retried_forever(self, client):
        """Apple retries non-2xx for days, and retrying will not make a payload
        well-formed."""
        r = client.post("/v1/subscriptions/apple/notifications", json={})
        assert r.status_code == 200


class TestAdminSurface:
    def test_a_normal_account_gets_a_404(self, client, auth):
        """404 rather than 403 — an unauthorised caller learns nothing about
        whether the route exists."""
        assert client.get("/v1/admin/metrics", headers=auth).status_code == 404

    def test_the_allowlist_is_exact(self, client, auth, db, user, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings(), "admin_emails", "boss@example.com", raising=False)
        for near_miss in ("boss@example.com.evil.test", "xboss@example.com", "BOSS@EXAMPLE.CO"):
            user.email = near_miss
            db.commit()
            assert client.get("/v1/admin/metrics", headers=auth).status_code == 404


class TestCostAbuse:
    """Every model call is money. An anonymous stranger with an account should
    not be able to spend more of it than the caps allow."""

    def test_oversized_bodies_are_refused(self, client, auth):
        for path, field in (("/v1/chat", "message"),
                            ("/v1/dashboard/today/adjust", "message")):
            r = client.post(path, headers=auth, json={field: "x" * 50_000})
            assert r.status_code == 422, f"{path} accepted 50k characters"

    def test_an_enormous_ingest_batch_is_refused(self, client, auth):
        one = {"source_record_id": "x", "measured_at": "2026-08-30T00:00:00Z",
               "metric": "steps", "value": 1.0}
        r = client.post("/v1/ingest/healthkit", headers=auth,
                        json={"body_metrics": [one] * 50_000})
        assert r.status_code == 422

    def test_chat_and_adjust_have_separate_ceilings(self, client, auth, db, user):
        from app.config import settings

        for _ in range(settings().chat_daily_message_limit):
            db.add(ChatMessage(user_id=user.id, role="user", content="x", kind="chat"))
        db.commit()
        q = client.get("/v1/chat/quota", headers=auth).json()
        assert q["remaining"] == 0
        # Spending one allowance must not silently spend the other.
        assert q["adjust_remaining"] == settings().adjust_daily_limit


class TestUnauthenticatedRoutes:
    def test_the_legal_route_is_an_allowlist_not_a_path(self, client):
        """It renders files by slug. A path is a traversal waiting to happen."""
        for attempt in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd", "privacy/../../secrets"):
            assert client.get(f"/legal/{attempt}").status_code in (404, 400)

    def test_only_the_known_documents_render(self, client):
        assert client.get("/legal/privacy").status_code == 200
        assert client.get("/legal/terms").status_code == 200
        assert client.get("/legal/anything-else").status_code == 404

    def test_the_oauth_callback_never_raises_to_the_user(self, client):
        """The user is inside a web view that only closes on the callback
        scheme. An exception strands them on a JSON dump."""
        for query in ("", "?error=access_denied", "?state=garbage&code=x", "?code=only"):
            r = client.get(f"/v1/providers/oura/callback{query}", follow_redirects=False)
            assert r.status_code in (302, 307), f"callback{query} returned {r.status_code}"

    def test_an_unknown_provider_callback_does_not_leak(self, client):
        r = client.get("/v1/providers/notreal/callback?code=x", follow_redirects=False)
        assert r.status_code in (302, 307)


class TestErrorsDoNotLeak:
    def test_secret_query_parameters_are_scrubbed_from_logs(self):
        from app.logging_filters import scrub

        line = ("GET /v1/providers/oura/callback"
                "?state=eyJhbGciOiJIUzI1NiJ9.LIVE&code=4/0ALIVECODE HTTP/1.1")
        out = scrub(line)
        assert "LIVE" not in out.replace("[redacted]", "")
        assert "/v1/providers/oura/callback" in out

    def test_the_export_never_contains_provider_credentials(self, client, auth, db, user):
        from app.models import OAuthToken

        db.add(OAuthToken(user_id=user.id, provider="strava",
                          access_token_enc="CIPHERTEXT-ACCESS",
                          refresh_token_enc="CIPHERTEXT-REFRESH"))
        db.commit()
        raw = client.get("/v1/me/export", headers=auth).text
        assert "CIPHERTEXT-ACCESS" not in raw
        assert "CIPHERTEXT-REFRESH" not in raw


class TestSignInDoesNotDescribeItsFailures:
    """The sign-in endpoints are unauthenticated. Echoing the exception hands an
    attacker a description of exactly how their forgery failed, which is how
    you iterate toward one that does not."""

    @pytest.mark.parametrize("path,field", [
        ("/v1/auth/apple", "identity_token"),
        ("/v1/auth/google", "id_token"),
    ])
    def test_the_reason_is_not_returned(self, client, path, field):
        r = client.post(path, json={field: "a.b.c"})
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert detail == "that sign-in could not be verified"
        # None of the library's vocabulary reaches the caller.
        for leak in ("padding", "Invalid", "Traceback", "jwt", "JWK", "signature"):
            assert leak.lower() not in detail.lower()
