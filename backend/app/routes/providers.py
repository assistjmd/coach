"""Provider OAuth connect/disconnect.

The callback has one governing rule, and it is the reason this module reads the
way it does:

    Once the provider has redirected the user back to us, EVERY code path must
    terminate in a `coach://` redirect. Never raise from `callback()`.

The user is sitting inside an `ASWebAuthenticationSession` web view at that
point. That view only closes when it sees the registered callback scheme, so an
HTTPException doesn't surface as an error — it strands the user on a JSON dump
with nothing but a Cancel button, and the app never learns what happened.

State is a signed, short-lived JWT (`app/auth/jwt.py`) rather than server-side
session state: it is multi-worker safe, needs no cache, and binds the flow to
one (user, provider) pair. Single-use is enforced separately via the
`oauth_state_uses` ledger.

No PKCE: Oura and Strava don't support it, the authorization code never touches
the device, and the exchange is server-side with a client secret. PKCE would add
ceremony without closing a threat we actually have.
"""

import logging
import urllib.parse
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.jwt import decode_state_token, issue_state_token
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import get_db
from app.models import OAuthStateUse, OAuthToken, User
from app.providers import get_provider
from app.providers.base import TokenSet

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/providers", tags=["providers"])

# Closed set. Never forward provider-supplied error text — it is
# attacker-influenceable and would render inside our UI.
FailureReason = Literal["denied", "invalid_state", "exchange_failed", "server_error"]


class AuthorizeResponse(BaseModel):
    authorize_url: str
    expires_at: datetime


def _deep_link(
    slug: str,
    *,
    reason: FailureReason | None = None,
    ref: str | None = None,
) -> RedirectResponse:
    params: dict[str, str] = {"provider": slug, "status": "error" if reason else "ok"}
    if reason:
        params["reason"] = reason
    if ref:
        params["ref"] = ref
    url = f"{settings().app_deep_link_scheme}://oauth/callback?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url, status_code=302)


@router.post("/{slug}/authorize", response_model=AuthorizeResponse)
def authorize(slug: str, user: User = Depends(current_user)) -> AuthorizeResponse:
    """Mint a one-time authorize URL for the signed-in user.

    POST, not GET: this issues a single-use credential, so it must never be
    cached, prefetched, or replayed by a browser. Returns JSON rather than a 302
    because the caller is `ASWebAuthenticationSession`, which cannot attach an
    `Authorization` header to a redirect it follows — the app fetches the URL
    with its bearer token, then hands the URL to the web view.
    """
    try:
        provider = get_provider(slug)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown provider: {slug}") from None
    if provider.info.availability != "live":
        # Listed in the app, not connectable yet. A 409 with the reason is what
        # the client shows; letting this through would build an authorize URL
        # against a client id we don't have.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            getattr(provider, "note", None)
            or f"{provider.info.display_name} isn't available yet",
        )
    if provider.info.auth_kind != "oauth2":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "provider does not use OAuth")

    state, expires_at = issue_state_token(user.id, slug)
    return AuthorizeResponse(authorize_url=provider.oauth_authorize_url(state), expires_at=expires_at)


def _consume_state(db: Session, claims_jti: uuid.UUID, user_id: uuid.UUID, slug: str) -> bool:
    """Record this state as used. False means it was already used (replay)."""
    stmt = (
        pg_insert(OAuthStateUse)
        .values(jti=claims_jti, user_id=user_id, provider=slug)
        .on_conflict_do_nothing(index_elements=["jti"])
        .returning(OAuthStateUse.jti)
    )
    won = db.execute(stmt).scalar_one_or_none() is not None
    db.commit()
    return won


@router.get("/{slug}/callback")
def callback(
    slug: str,
    background: BackgroundTasks,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    # All three are optional: a user tapping "Deny" comes back with `error` and
    # no `code`. Declaring `code` required would return a 422 validation dump
    # into the web view instead of releasing the user back to the app.
    ref = uuid.uuid4().hex[:12]

    if not state:
        log.warning("oauth callback ref=%s provider=%s: missing state", ref, slug)
        return _deep_link(slug, reason="invalid_state", ref=ref)

    try:
        claims = decode_state_token(state)
    except ValueError as e:
        # Expired, tampered, wrong audience — all one reason to the client.
        # Distinguishing them only helps someone probing the endpoint.
        log.warning("oauth callback ref=%s provider=%s: bad state (%s)", ref, slug, e)
        return _deep_link(slug, reason="invalid_state", ref=ref)

    if claims.provider != slug:
        log.warning(
            "oauth callback ref=%s: state bound to %s but called on %s",
            ref, claims.provider, slug,
        )
        return _deep_link(slug, reason="invalid_state", ref=ref)

    # Consume before branching, so a state is spent whatever the outcome.
    if not _consume_state(db, claims.jti, claims.user_id, slug):
        log.warning("oauth callback ref=%s provider=%s: state replay jti=%s", ref, slug, claims.jti)
        return _deep_link(slug, reason="invalid_state", ref=ref)

    if error:
        reason: FailureReason = "denied" if error == "access_denied" else "exchange_failed"
        log.info("oauth callback ref=%s provider=%s: provider error %r", ref, slug, error)
        return _deep_link(slug, reason=reason, ref=ref)

    if not code:
        log.warning("oauth callback ref=%s provider=%s: no code and no error", ref, slug)
        return _deep_link(slug, reason="exchange_failed", ref=ref)

    try:
        provider = get_provider(slug)
    except KeyError:
        log.warning("oauth callback ref=%s: unknown provider %s", ref, slug)
        return _deep_link(slug, reason="invalid_state", ref=ref)

    try:
        tokenset = provider.exchange_code(code)
    except Exception:
        log.exception("oauth callback ref=%s provider=%s: code exchange failed", ref, slug)
        return _deep_link(slug, reason="exchange_failed", ref=ref)

    try:
        token_id = _persist(db, claims.user_id, slug, tokenset)
    except Exception:
        log.exception("oauth callback ref=%s provider=%s: token persist failed", ref, slug)
        db.rollback()
        return _deep_link(slug, reason="server_error", ref=ref)

    # Pull immediately instead of waiting for the 30-minute scheduler tick.
    # Without this a user finishes the OAuth flow and lands on an app with
    # nothing in it, with no indication that anything is coming — which reads as
    # a failed connection. Runs after the response so the redirect back into the
    # app isn't held open for the length of a first backfill.
    background.add_task(_sync_after_connect, token_id, slug, ref)

    log.info("oauth callback ref=%s provider=%s: connected user=%s", ref, slug, claims.user_id)
    return _deep_link(slug)


def _sync_after_connect(token_id: uuid.UUID, slug: str, ref: str) -> None:
    """First sync for a newly connected provider.

    Never raises: this runs detached from the request, and the user is already
    back in the app. A failure is recorded on the token by `sync_one` and shown
    on the Sources screen.
    """
    from app.jobs.sync import sync_one

    try:
        written = sync_one(token_id)
        log.info("first sync ref=%s provider=%s: %d records", ref, slug, written)
    except Exception:
        log.exception("first sync ref=%s provider=%s failed", ref, slug)


def _persist(db: Session, user_id: uuid.UUID, slug: str, tokenset: TokenSet) -> uuid.UUID:
    row = (
        db.query(OAuthToken)
        .filter(OAuthToken.user_id == user_id, OAuthToken.provider == slug)
        .one_or_none()
    )
    if row is None:
        row = OAuthToken(user_id=user_id, provider=slug)
        db.add(row)
    row.access_token_enc = encrypt(tokenset.access_token)
    row.refresh_token_enc = encrypt(tokenset.refresh_token) if tokenset.refresh_token else None
    row.expires_at = tokenset.expires_at
    row.scope = tokenset.scope
    db.commit()
    db.refresh(row)
    return row.id


class SyncResponse(BaseModel):
    provider: str
    records: int


@router.post("/{slug}/sync", response_model=SyncResponse)
def sync_now(
    slug: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SyncResponse:
    """Pull from a connected provider immediately.

    The scheduler only runs every 30 minutes, which is far too slow a loop for
    "I just connected it, where's my data" — both for the user right after
    onboarding and for anyone developing against this.
    """
    # Imported here: app.jobs.sync imports load_tokenset from this module, so a
    # module-level import would be circular.
    from app.jobs.sync import sync_one

    row = (
        db.query(OAuthToken)
        .filter(OAuthToken.user_id == user.id, OAuthToken.provider == slug)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{slug} is not connected")
    try:
        written = sync_one(row.id)
    except Exception as e:
        log.exception("manual sync failed for user=%s provider=%s", user.id, slug)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"sync failed: {e}") from e
    return SyncResponse(provider=slug, records=written)


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
