"""Verify a Google ID token sent from the iOS Google Sign-In SDK."""

from __future__ import annotations

from typing import Any

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.config import settings

_request = google_requests.Request()


def verify_id_token(token: str) -> dict[str, Any]:
    cfg = settings()
    audiences = [a for a in (cfg.google_client_id_ios, cfg.google_client_id_web) if a]
    last_err: Exception | None = None
    for aud in audiences or [None]:  # if no audiences configured, do unaudienced verify (dev only)
        try:
            return google_id_token.verify_oauth2_token(token, _request, audience=aud)
        except Exception as e:
            last_err = e
    raise ValueError("invalid google id token") from last_err
