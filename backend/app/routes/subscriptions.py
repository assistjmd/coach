"""Subscription verification and Apple's server notifications.

Two ways a subscription's state reaches us, and both are needed:

- The app reports a purchase the moment it happens, so access opens without
  waiting for a webhook.
- Apple posts a notification on every renewal, cancellation, refund and billing
  failure, most of which the app is never running to observe.

Neither is trusted on its face. Both carry a JWS payload signed by Apple, and
both are verified against Apple's certificate chain before a single field is
read. A client saying "I subscribed" is a claim; Apple's signature is the proof.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, select
import uuid

from app.auth.deps import current_user
from app.config import settings
from app.db import get_db
from app.analytics.pitch import build as build_pitch
from app.entitlement import evaluate, allowed_environments
from app.models import Subscription, User
from app.subscriptions.apple import VerificationError, verify_notification, verify_transaction

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])


class PitchResponse(BaseModel):
    """The case for subscribing, made from the athlete's own fortnight."""
    lines: list[str] = []
    sessions_completed: int | None = None
    sessions_asked: int | None = None
    workouts_logged: int | None = None
    readiness_change: int | None = None
    days_with_coach: int = 0


class StatusResponse(BaseModel):
    # "subscribed" | "trial" | "expired"
    state: str
    trial_days_left: int
    expires_at: datetime | None
    product_id: str | None
    # False while the products do not exist in App Store Connect, so the client
    # knows not to show a paywall it cannot satisfy.
    enforced: bool
    pitch: PitchResponse = PitchResponse()


class VerifyBody(BaseModel):
    # The JWS representation of a StoreKit 2 transaction.
    signed_transaction: str = Field(min_length=1, max_length=8000)


def _status(db: Session, user: User) -> StatusResponse:
    e = evaluate(db, user)
    return StatusResponse(
        state=e.state,
        trial_days_left=e.trial_days_left,
        expires_at=e.expires_at,
        product_id=e.product_id,
        enforced=settings().subscriptions_enforced,
        pitch=PitchResponse(**asdict(build_pitch(db, user))),
    )


@router.get("/status", response_model=StatusResponse)
def subscription_status(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> StatusResponse:
    return _status(db, user)


@router.post("/verify", response_model=StatusResponse)
def verify(
    body: VerifyBody,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StatusResponse:
    """Record a purchase the app has just completed."""
    try:
        tx = verify_transaction(body.signed_transaction)
    except VerificationError as e:
        log.warning("rejected transaction for user %s: %s", user.id, e)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "that purchase could not be verified"
        ) from None

    _upsert(db, user.id, tx)
    return _status(db, user)


@router.post("/apple/notifications", status_code=status.HTTP_200_OK)
async def apple_notifications(request: Request, db: Session = Depends(get_db)) -> dict:
    """App Store Server Notifications V2.

    Unauthenticated by necessity — Apple has no bearer token to present — so the
    JWS signature is the whole of the authentication. Always answers 200 on a
    payload we cannot act on: Apple retries non-2xx for days, and retrying will
    not make an unknown transaction known.
    """
    try:
        body = await request.json()
    except ValueError:
        return {"ok": False, "reason": "invalid payload"}
    if not isinstance(body, dict):
        return {"ok": False, "reason": "invalid payload"}
    signed = body.get("signedPayload")
    if not signed:
        return {"ok": False, "reason": "no payload"}
    if not isinstance(signed, str) or len(signed) > 64000:
        raise HTTPException(400, "invalid payload")

    try:
        notification = verify_notification(signed)
    except VerificationError as e:
        # A signature failure is either an attacker or a certificate rotation we
        # have not picked up. Both are worth an alert; neither is worth a retry.
        log.error("rejected apple notification: %s", e)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad signature") from None

    tx = notification.transaction
    if tx is None:
        return {"ok": True, "note": "nothing to apply"}

    _lock_transaction(db, tx.original_transaction_id)
    sub = (
        db.query(Subscription)
        .filter(Subscription.original_transaction_id == tx.original_transaction_id)
        .one_or_none()
    )
    if sub is None:
        try:
            owner = db.get(User, uuid.UUID(str(tx.app_account_token)))
        except (ValueError, AttributeError, TypeError):
            owner = None
        if owner is not None:
            _upsert(db, owner.id, tx)
            return {"ok": True}
        # A renewal for a subscription we never saw verified. Nothing to attach
        # it to — the user row is only known via the app's own verify call.
        log.warning(
            "notification %s for unknown transaction %s",
            notification.notification_type,
            tx.original_transaction_id,
        )
        return {"ok": True, "note": "unknown subscription"}

    if tx.environment != sub.environment or (
        tx.app_account_token and not _same_account(tx.app_account_token, sub.user_id)
    ):
        raise HTTPException(409, "notification does not match the subscription account")
    _apply(sub, tx, notification.notification_type)
    db.commit()
    return {"ok": True}


def _same_account(token: str | None, user_id) -> bool:
    """Whether Apple's signed account token names this Coach account.

    Compared as UUIDs rather than strings. Swift renders a UUID in upper case
    and Python in lower, so `str(user_id) == token` rejects a purchase the
    athlete legitimately made from their own phone.
    """
    if not token:
        return False
    try:
        return uuid.UUID(str(token)) == uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        return False


def _upsert(db: Session, user_id, tx) -> Subscription:
    if tx.environment not in allowed_environments(user_id):
        raise HTTPException(400, "this purchase environment is not enabled for this account")
    if tx.signed_at is None:
        raise HTTPException(400, "purchase is missing its signed date")
    _lock_transaction(db, tx.original_transaction_id)
    sub = (
        db.query(Subscription)
        .filter(Subscription.original_transaction_id == tx.original_transaction_id)
        .one_or_none()
    )
    if sub is None:
        # Apple's signature must bind a new purchase to this Coach account.
        # Knowing someone else's valid JWS is not proof of owning their account.
        if not _same_account(tx.app_account_token, user_id):
            raise HTTPException(409, "this purchase is not linked to this account")
        sub = Subscription(
            user_id=user_id, original_transaction_id=tx.original_transaction_id
        )
        db.add(sub)
    elif sub.user_id != user_id:
        raise HTTPException(409, "this purchase belongs to another account; sign in to restore it")
    elif tx.app_account_token and not _same_account(tx.app_account_token, user_id):
        raise HTTPException(409, "this purchase is not linked to this account")

    _apply(sub, tx, None)
    db.commit()
    db.refresh(sub)
    return sub


def _apply(sub: Subscription, tx, notification_type: str | None) -> None:
    if sub.signed_at and (tx.signed_at is None or tx.signed_at <= sub.signed_at):
        return
    # Even a newly signed copy of an older renewal must not shorten the
    # currently paid period, or revoke a different, later transaction.
    if (sub.expires_at and tx.expires_at and tx.expires_at < sub.expires_at
            and tx.transaction_id != sub.transaction_id):
        return
    sub.signed_at = tx.signed_at
    sub.transaction_id = tx.transaction_id
    sub.product_id = tx.product_id
    sub.environment = tx.environment
    sub.auto_renew = tx.auto_renew
    if tx.expires_at is not None:
        sub.expires_at = tx.expires_at
    if tx.revoked_at is not None or notification_type in ("REFUND", "REVOKE"):
        # A refund ends access now, not at the period end it was notionally
        # paid through.
        sub.revoked = True
        sub.expires_at = min(
            sub.expires_at or datetime.now(timezone.utc), datetime.now(timezone.utc)
        )
    else:
        sub.revoked = False


def _lock_transaction(db: Session, original: str) -> None:
    # Includes first insert, for which SELECT FOR UPDATE has no row to lock.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(original, 0))))
