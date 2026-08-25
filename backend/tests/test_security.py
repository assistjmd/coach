"""Security regressions.

Each of these encodes a finding from the pre-submission audit. They are cheap to
break silently — a log line changes, a debug flag flips, a Field loses a bound —
and expensive to notice.
"""

import logging

import pytest

from app.config import settings
from app.logging_filters import RedactSecrets, scrub


class TestCredentialsStayOutOfLogs:
    """The OAuth callback carries a live authorization code in its query string,
    and uvicorn's access log records the full request line."""

    def test_authorization_code_is_redacted(self):
        line = (
            'GET /v1/providers/google_health/callback'
            '?state=eyJhbGciOiJIUzI1NiJ9.abc&code=4/0AXEQxIC9tMsFC3q&scope=x HTTP/1.1'
        )
        out = scrub(line)
        assert "4/0AXEQxIC9tMsFC3q" not in out
        assert "eyJhbGciOiJIUzI1NiJ9.abc" not in out
        assert "code=[redacted]" in out
        assert "state=[redacted]" in out
        # The path has to survive or the log stops being useful.
        assert "/v1/providers/google_health/callback" in out
        assert "scope=x" in out

    @pytest.mark.parametrize(
        "param", ["code", "state", "access_token", "id_token", "refresh_token"]
    )
    def test_every_secret_parameter_is_covered(self, param):
        assert "s3cret" not in scrub(f"GET /x?{param}=s3cret")

    def test_the_filter_rewrites_log_args_not_just_the_message(self):
        """uvicorn builds its access line from args. Rewriting record.msg alone
        would leave the credential untouched."""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s" %d',
            args=("1.2.3.4", "GET /cb?code=LIVE_CODE HTTP/1.1", 302),
            exc_info=None,
        )
        RedactSecrets().filter(record)
        assert "LIVE_CODE" not in record.getMessage()
        assert "[redacted]" in record.getMessage()


class TestProductionSurface:
    def test_docs_are_closed_outside_dev(self):
        """The interactive docs publish every route and field to anyone asking."""
        from app.main import app

        if settings().app_env == "dev":
            pytest.skip("docs are intentionally open in dev")
        assert app.docs_url is None
        assert app.openapi_url is None
        assert app.redoc_url is None

    def test_security_headers_are_present(self, client):
        r = client.get("/healthz")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "max-age=" in r.headers["Strict-Transport-Security"]
        assert r.headers["Referrer-Policy"] == "no-referrer"
        # The legal pages are the only HTML served, and they are self-contained.
        assert "default-src 'none'" in r.headers["Content-Security-Policy"]


class TestInputIsBounded:
    """The daily message count is not the only cost: 25 unbounded messages can
    be 25 megabytes of input tokens."""

    def test_an_oversized_chat_message_is_rejected(self, client, auth):
        r = client.post("/v1/chat", headers=auth, json={"message": "x" * 5000})
        assert r.status_code == 422

    def test_an_oversized_adjust_message_is_rejected(self, client, auth):
        r = client.post(
            "/v1/dashboard/today/adjust", headers=auth, json={"message": "x" * 5000}
        )
        assert r.status_code == 422

    def test_an_oversized_ingest_batch_is_rejected(self, client, auth):
        one = {
            "source_record_id": "x",
            "measured_at": "2026-08-25T00:00:00Z",
            "metric": "steps",
            "value": 1.0,
        }
        r = client.post(
            "/v1/ingest/healthkit",
            headers=auth,
            json={"body_metrics": [one] * 5000},
        )
        assert r.status_code == 422


class TestAuthentication:
    def test_no_token_is_rejected(self, client):
        assert client.get("/v1/me").status_code in (401, 403)

    def test_a_forged_token_is_rejected(self, client):
        r = client.get("/v1/me", headers={"Authorization": "Bearer not.a.token"})
        assert r.status_code == 401

    def test_an_unsigned_token_is_rejected(self, client):
        """alg=none must never be accepted. The decoder pins the algorithm."""
        import base64
        import json

        def part(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        forged = (
            part({"alg": "none", "typ": "JWT"})
            + "."
            + part({"sub": "00000000-0000-0000-0000-000000000001", "aud": "session"})
            + "."
        )
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401

    def test_a_state_token_cannot_be_used_as_a_session(self, client, user):
        """Different audiences and different signing keys. A state token travels
        through provider logs and browser history; accepting one as a bearer
        credential would be privilege escalation."""
        from app.auth.jwt import issue_state_token

        token, _ = issue_state_token(user.id, "oura")
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
