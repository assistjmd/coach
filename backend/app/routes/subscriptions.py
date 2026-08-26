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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.config import settings
from app.db import get_db
from app.entitlement import evaluate
from app.models import Subscription, User
from app.subscriptions.apple import VerificationError, verify_notification, verify_transaction

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])


class StatusResponse(BaseModel):
    # "subscribed" | "trial" | "expired"
    state: str
    trial_days_left: int
    expires_at: datetime | None
    product_id: str | None
    # False while the products do not exist in App Store Connect, so the client
    # knows not to show a paywall it cannot satisfy.
    enforced: bool


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
    body = await request.json()
    signed = body.get("signedPayload")
    if not signed:
        return {"ok": False, "reason": "no payload"}

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

    sub = (
        db.query(Subscription)
        .filter(Subscription.original_transaction_id == tx.original_transaction_id)
        .one_or_none()
    )
    if sub is None:
        # A renewal for a subscription we never saw verified. Nothing to attach
        # it to — the user row is only known via the app's own verify call.
        log.warning(
            "notification %s for unknown transaction %s",
            notification.notification_type,
            tx.original_transaction_id,
        )
        return {"ok": True, "note": "unknown subscription"}

    _apply(sub, tx, notification.notification_type)
    db.commit()
    return {"ok": True}


def _upsert(db: Session, user_id, tx) -> Subscription:
    sub = (
        db.query(Subscription)
        .filter(Subscription.original_transaction_id == tx.original_transaction_id)
        .one_or_none()
    )
    if sub is None:
        sub = Subscription(
            user_id=user_id, original_transaction_id=tx.original_transaction_id
        )
        db.add(sub)
    elif sub.user_id != user_id:
        # The same Apple ID restoring onto a second Coach Will account. Move the
        # entitlement rather than duplicating it: one purchase, one active
        # subscription, and the most recent account is the one in use.
        log.info("subscription %s moved to user %s", tx.original_transaction_id, user_id)
        sub.user_id = user_id

    _apply(sub, tx, None)
    db.commit()
    db.refresh(sub)
    return sub


def _apply(sub: Subscription, tx, notification_type: str | None) -> None:
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
