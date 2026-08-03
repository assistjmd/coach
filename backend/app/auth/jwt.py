"""Token issuing/decoding.

Two distinct token families share this module, and they must never be
interchangeable:

- **Session tokens** (`aud="session"`) are bearer credentials sent in the
  `Authorization` header. They are long-lived and grant full access to a user.
- **OAuth state tokens** (`aud="oauth_state"`) are short-lived CSRF binders that
  travel in a URL query string — through the provider's servers, their access
  logs, browser history, and the consent page's `Referer` header.

A state token that a session decoder accepts is a privilege escalation. We defend
twice: the `aud` claim is validated on both paths, *and* state tokens are signed
with a key derived from `jwt_secret` so a state token cannot even produce a valid
signature against the session key.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.config import settings

ISSUER = "coach"
AUD_SESSION = "session"
AUD_OAUTH_STATE = "oauth_state"

# OAuth state must outlive a slow consent page (provider login, 2FA) but bound
# the replay window. Single-use enforcement lives in `oauth_state_uses`.
STATE_TTL = timedelta(minutes=10)

_STATE_KEY_INFO = b"oauth_state_v1"


def _state_key() -> str:
    """Key for state tokens, derived from `jwt_secret`.

    Separate keys make signature confusion structurally impossible rather than
    dependent on remembering to check `aud`.
    """
    return hmac.new(settings().jwt_secret.encode(), _STATE_KEY_INFO, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class StateClaims:
    user_id: uuid.UUID
    provider: str
    jti: uuid.UUID


def issue_jwt(user_id: uuid.UUID) -> str:
    cfg = settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=cfg.jwt_expires_hours)).timestamp()),
        "iss": ISSUER,
        "aud": AUD_SESSION,
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_alg)


def decode_jwt(token: str) -> uuid.UUID:
    """Decode a *session* token. Rejects state tokens."""
    cfg = settings()
    try:
        payload = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=[cfg.jwt_alg],
            audience=AUD_SESSION,
            issuer=ISSUER,
            options={"require_aud": True, "require_exp": True, "require_sub": True},
        )
    except JWTError as e:
        raise ValueError("invalid token") from e
    return uuid.UUID(payload["sub"])


def issue_state_token(user_id: uuid.UUID, provider: str) -> tuple[str, datetime]:
    """Mint a single-use OAuth state token. Returns (token, expires_at)."""
    cfg = settings()
    now = datetime.now(timezone.utc)
    expires_at = now + STATE_TTL
    payload = {
        "sub": str(user_id),
        "provider": provider,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": ISSUER,
        "aud": AUD_OAUTH_STATE,
    }
    return jwt.encode(payload, _state_key(), algorithm=cfg.jwt_alg), expires_at


def decode_state_token(token: str) -> StateClaims:
    """Decode an *OAuth state* token. Rejects session tokens.

    Raises ValueError on anything malformed, expired, tampered, or of the wrong
    audience. Callers must not distinguish these to the user — see the
    `invalid_state` reason in `app/routes/providers.py`.
    """
    cfg = settings()
    try:
        payload = jwt.decode(
            token,
            _state_key(),
            algorithms=[cfg.jwt_alg],
            audience=AUD_OAUTH_STATE,
            issuer=ISSUER,
            options={
                "require_aud": True,
                "require_exp": True,
                "require_sub": True,
                "require_jti": True,
            },
        )
    except JWTError as e:
        raise ValueError("invalid state token") from e
    try:
        return StateClaims(
            user_id=uuid.UUID(payload["sub"]),
            provider=payload["provider"],
            jti=uuid.UUID(payload["jti"]),
        )
    except (KeyError, ValueError) as e:
        raise ValueError("malformed state token") from e
