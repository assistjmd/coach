"""Token audience separation.

The two cross-audience tests are the entire reason the stateless OAuth design is
safe. State tokens travel in URL query strings — through the provider's servers,
their logs, browser history, and the consent page's `Referer` header. If one of
those were accepted as a session bearer token, leaking a state would mean handing
over the user's account.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt as jose_jwt

from app.auth.jwt import (
    AUD_OAUTH_STATE,
    AUD_SESSION,
    ISSUER,
    decode_jwt,
    decode_state_token,
    issue_jwt,
    issue_state_token,
)
from app.config import settings


def test_session_token_round_trips():
    uid = uuid.uuid4()
    assert decode_jwt(issue_jwt(uid)) == uid


def test_state_token_round_trips():
    uid = uuid.uuid4()
    token, expires_at = issue_state_token(uid, "oura")
    claims = decode_state_token(token)
    assert claims.user_id == uid
    assert claims.provider == "oura"
    assert isinstance(claims.jti, uuid.UUID)
    assert expires_at > datetime.now(timezone.utc)


def test_each_state_token_has_a_distinct_jti():
    uid = uuid.uuid4()
    a, _ = issue_state_token(uid, "oura")
    b, _ = issue_state_token(uid, "oura")
    assert decode_state_token(a).jti != decode_state_token(b).jti


def test_state_token_is_rejected_as_a_session_token():
    """A leaked OAuth state must not be usable as a bearer credential."""
    token, _ = issue_state_token(uuid.uuid4(), "oura")
    with pytest.raises(ValueError):
        decode_jwt(token)


def test_session_token_is_rejected_as_state():
    """And a bearer token must not be usable to forge a connect callback."""
    with pytest.raises(ValueError):
        decode_state_token(issue_jwt(uuid.uuid4()))


def test_expired_state_is_rejected():
    cfg = settings()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    forged = jose_jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "provider": "oura",
            "jti": str(uuid.uuid4()),
            "exp": int(past.timestamp()),
            "iss": ISSUER,
            "aud": AUD_OAUTH_STATE,
        },
        cfg.jwt_secret,
        algorithm=cfg.jwt_alg,
    )
    with pytest.raises(ValueError):
        decode_state_token(forged)


def test_state_signed_with_the_session_key_is_rejected():
    """State uses a derived key, so signing with `jwt_secret` isn't enough.

    This is the belt to the audience check's braces: even a token carrying the
    right `aud` fails if it wasn't signed with the state key.
    """
    cfg = settings()
    forged = jose_jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "provider": "oura",
            "jti": str(uuid.uuid4()),
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
            "iss": ISSUER,
            "aud": AUD_OAUTH_STATE,
        },
        cfg.jwt_secret,
        algorithm=cfg.jwt_alg,
    )
    with pytest.raises(ValueError):
        decode_state_token(forged)


def test_tokens_without_an_audience_are_rejected():
    """Pre-audience tokens must not keep working — that would defeat the split."""
    cfg = settings()
    legacy = jose_jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
            "iss": ISSUER,
        },
        cfg.jwt_secret,
        algorithm=cfg.jwt_alg,
    )
    with pytest.raises(ValueError):
        decode_jwt(legacy)


def test_wrong_secret_is_rejected():
    forged = jose_jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
            "iss": ISSUER,
            "aud": AUD_SESSION,
        },
        "not-the-real-secret",
        algorithm=settings().jwt_alg,
    )
    with pytest.raises(ValueError):
        decode_jwt(forged)
