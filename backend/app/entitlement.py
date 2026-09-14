"""Who is allowed to use the expensive parts of the app.

Three states, in the order they are checked:

1. An active subscription verified against Apple's signature.
2. A trial that has not run out.
3. Neither, which means the paywall.

The trial runs on our clock rather than as a StoreKit introductory offer. An
intro offer needs a payment method on day zero, and asking the first users of an
unknown app for a card before they have seen a single brief costs more installs
than it earns subscriptions. The cost of being wrong is fourteen days of tokens,
about forty cents.

Entitlement is never taken from the client. The app reports that a purchase
happened; Apple's signature over the transaction is what makes it true.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.consent import require_consent, has_consent
from app.config import settings
from app.db import get_db
from app.models import Subscription, User


@dataclass(frozen=True)
class Entitlement:
    # "subscribed" | "trial" | "expired"
    state: str
    trial_ends_at: datetime | None
    expires_at: datetime | None
    product_id: str | None

    @property
    def allowed(self) -> bool:
        return self.state in ("subscribed", "trial")

    @property
    def trial_days_left(self) -> int:
        if self.state != "trial" or self.trial_ends_at is None:
            return 0
        remaining = (self.trial_ends_at - datetime.now(timezone.utc)).total_seconds()
        # Ceiling: while access is genuinely still open the copy must not say
        # zero days left. A trial with eleven hours to run is "1 day".
        return max(0, math.ceil(remaining / 86400))


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def ensure_trial(db: Session, user: User) -> None:
    """Start the clock on first sight.

    Backfills accounts that predate subscriptions, so existing testers get a
    full trial from now rather than one that expired before it was invented.
    """
    if user.trial_ends_at is None:
        user.trial_ends_at = datetime.now(timezone.utc) + timedelta(
            days=settings().trial_days
        )
        db.commit()


def evaluate(db: Session, user: User) -> Entitlement:
    ensure_trial(db, user)
    now = datetime.now(timezone.utc)

    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == user.id, Subscription.revoked.is_(False))
        .filter(Subscription.environment.in_(allowed_environments(user.id)))
        .order_by(Subscription.expires_at.desc().nullslast())
        .first()
    )
    sub_expires = _aware(sub.expires_at) if sub else None
    if sub is not None and sub_expires is not None and sub_expires > now:
        return Entitlement("subscribed", _aware(user.trial_ends_at), sub_expires, sub.product_id)

    trial_ends = _aware(user.trial_ends_at)
    if trial_ends is not None and trial_ends > now:
        return Entitlement("trial", trial_ends, None, None)

    return Entitlement("expired", trial_ends, sub_expires, sub.product_id if sub else None)


def require(
    user: User = Depends(require_consent), db: Session = Depends(get_db)
) -> User:
    """Gate for anything that costs money to serve.

    Applied to brief generation, chat and session adjustment — the calls that
    spend tokens. Reading history the athlete already paid to generate stays
    open: taking away data they created is a hostage tactic, and the export
    endpoint has to keep working regardless so the erasure and portability
    promises in the privacy policy remain true.
    """
    if not settings().subscriptions_enforced:
        return user
    if not evaluate(db, user).allowed:
        # 402 rather than 403: the client shows a paywall, and this is the one
        # status that means exactly that.
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Your trial has ended. Subscribe to keep training with Will.",
        )
    return user


def can_generate(db: Session, user: User) -> bool:
    """The same billing decision for API calls and unattended model work."""
    return has_consent(db, user) and (
        not settings().subscriptions_enforced or evaluate(db, user).allowed
    )


def allowed_environments(user_id) -> tuple[str, ...]:
    """Which Apple environments may grant this account access.

    A production account gets Production receipts and nothing else, so a
    sandbox purchase cannot hand out a real subscription. Two exceptions are
    deliberate: a listed account id, and the wildcard `*`.

    The wildcard exists for App Review. A reviewer installs the build, signs in
    — creating an account whose id nobody could have listed in advance — and
    buys with a sandbox Apple ID, because review always transacts in Sandbox.
    Refusing that is a submission rejected for an in-app purchase that "could
    not be completed". A public App Store build only ever produces Production
    receipts, so the wildcard's reach is limited to builds we hand out.
    """
    cfg = settings()
    testers = {v.strip() for v in cfg.sandbox_subscription_user_ids.split(",") if v.strip()}
    if cfg.app_env != "prod" or "*" in testers or str(user_id) in testers:
        return ("Production", "Sandbox")
    return ("Production",)
