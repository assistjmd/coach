"""Verify the identity token returned by Sign In with Apple on iOS.

iOS sends the `identityToken` from ASAuthorizationAppleIDCredential. We verify it
against Apple's JWKS, check the audience matches our bundle ID, and return the
stable Apple `sub` claim plus optional email.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt as pyjwt
from jwt import PyJWKClient

from app.config import settings

_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_ISS = "https://appleid.apple.com"

_jwk_client: PyJWKClient | None = None


def _client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(_APPLE_JWKS_URL, cache_keys=True)
    return _jwk_client


def verify_identity_token(identity_token: str) -> dict[str, Any]:
    cfg = settings()
    signing_key = _client().get_signing_key_from_jwt(identity_token).key
    claims = pyjwt.decode(
        identity_token,
        signing_key,
        algorithms=["RS256"],
        audience=cfg.apple_bundle_id,
        issuer=_APPLE_ISS,
    )
    if claims.get("exp", 0) < time.time():
        raise ValueError("apple token expired")
    return claims


# Warm the JWKS cache at startup to avoid first-request latency.
def warm_cache() -> None:
    try:
        httpx.get(_APPLE_JWKS_URL, timeout=5)
    except Exception:
        pass
