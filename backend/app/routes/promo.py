"""Redeeming a promo code.

Extends the free trial rather than granting a subscription. A subscription is
Apple's to grant and ours to verify; inventing one here would put a row in the
table that no Apple signature backs, and every count of paying customers would
be wrong from then on.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.entitlement import ensure_trial
from app.models import PromoCode, PromoRedemption, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/promo", tags=["promo"])


class RedeemBody(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class RedeemResponse(BaseModel):
    extra_days: int
    trial_ends_at: datetime
    message: str


@router.post("/redeem", response_model=RedeemResponse)
def redeem(
    body: RedeemBody,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RedeemResponse:
    # Uppercased and trimmed: someone typing this on a phone keyboard will get
    # the case wrong, and telling them their correct code is wrong is a support
    # email you did not need.
    code = body.code.strip().upper()
    promo = db.query(PromoCode).filter(PromoCode.code == code).one_or_none()

    today = datetime.now(timezone.utc).date()
    expired = promo is not None and promo.expires_on is not None and promo.expires_on < today
    if promo is None or not promo.active or expired:
        # One message for all three. Distinguishing "no such code" from
        # "expired" tells someone probing which guesses were close.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That code isn't valid.")

    if promo.max_redemptions is not None:
        used = (
            db.query(PromoRedemption)
            .filter(PromoRedemption.promo_code_id == promo.id)
            .count()
        )
        if used >= promo.max_redemptions:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "That code has been fully claimed."
            )

    ensure_trial(db, user)
    current = user.trial_ends_at
    if current is not None and current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    # Extended from whichever is later. Someone redeeming three days early gets
    # a month on top of what they had; someone redeeming a week after it lapsed
    # gets a month from now, not a month from a date already gone.
    base = max(current or datetime.now(timezone.utc), datetime.now(timezone.utc))
    extended = base + timedelta(days=promo.extra_days)

    try:
        db.add(PromoRedemption(
            user_id=user.id, promo_code_id=promo.id, extended_to=extended
        ))
        user.trial_ends_at = extended
        db.commit()
    except IntegrityError:
        # The unique constraint did its job: this account has used this code.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You've already used that code."
        ) from None

    log.info("promo %s redeemed by user %s until %s", code, user.id, extended.date())
    return RedeemResponse(
        extra_days=promo.extra_days,
        trial_ends_at=extended,
        message=f"Done — {promo.extra_days} more days on me.",
    )
