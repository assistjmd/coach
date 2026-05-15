import secrets
import urllib.parse
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.jwt import decode_jwt
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import get_db
from app.models import OAuthToken, User
from app.providers import get_provider
from app.providers.base import TokenSet

router = APIRouter(prefix="/v1/providers", tags=["providers"])

# In-memory OAuth state cache (state -> user_id, expires). Fine for a single-process
# MVP; move to Redis/DB if we ever run multiple workers.
_STATE_CACHE: dict[str, tuple[str, datetime]] = {}


def _purge_states() -> None:
    now = datetime.now(timezone.utc)
    expired = [k for k, (_, exp) in _STATE_CACHE.items() if exp < now]
    for k in expired:
        _STATE_CACHE.pop(k, None)


@router.get("/{slug}/authorize")
def authorize(slug: str, user: User = Depends(current_user)) -> RedirectResponse:
    try:
        provider = get_provider(slug)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown provider: {slug}") from None
    if provider.info.auth_kind != "oauth2":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "provider does not use OAuth")

    _purge_states()
    state = secrets.token_urlsafe(24)
    _STATE_CACHE[state] = (
        str(user.id),
        datetime.now(timezone.utc).replace(microsecond=0),
    )
    url = provider.oauth_authorize_url(state)
    return RedirectResponse(url, status_code=302)


@router.get("/{slug}/callback")
def callback(
    slug: str,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    _purge_states()
    entry = _STATE_CACHE.pop(state, None)
    if entry is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired state")
    user_id, _ = entry

    try:
        provider = get_provider(slug)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider") from None

    tokenset = provider.exchange_code(code)
    existing = (
        db.query(OAuthToken)
        .filter(OAuthToken.user_id == user_id, OAuthToken.provider == slug)
        .one_or_none()
    )
    if existing is None:
        existing = OAuthToken(user_id=user_id, provider=slug)
        db.add(existing)
    existing.access_token_enc = encrypt(tokenset.access_token)
    existing.refresh_token_enc = encrypt(tokenset.refresh_token) if tokenset.refresh_token else None
    existing.expires_at = tokenset.expires_at
    existing.scope = tokenset.scope
    db.commit()

    deep_link = (
        f"{settings().app_deep_link_scheme}://oauth/callback"
        f"?provider={urllib.parse.quote(slug)}&status=ok"
    )
    return RedirectResponse(deep_link, status_code=302)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    slug: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    db.query(OAuthToken).filter(
        OAuthToken.user_id == user.id, OAuthToken.provider == slug
    ).delete()
    db.commit()


def load_tokenset(token: OAuthToken) -> TokenSet:
    return TokenSet(
        access_token=decrypt(token.access_token_enc),
        refresh_token=decrypt(token.refresh_token_enc) if token.refresh_token_enc else None,
        expires_at=token.expires_at,
        scope=token.scope,
    )
