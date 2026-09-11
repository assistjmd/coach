"""Verify Apple's signed transactions and notifications.

Everything Apple sends about a purchase is a JWS whose header carries the
certificate chain that signed it. Verification means checking that chain up to
Apple's own root, and it is the only thing standing between a real subscription
and a forged one — a decoded-but-unverified payload is attacker-controlled JSON.

Apple's own library does the chain work. What is here is the mapping into the
shape the rest of the app wants, plus the root certificates it needs.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import (
    SignedDataVerifier,
    VerificationException,
)

from app.config import settings

log = logging.getLogger(__name__)

_ROOTS_DIR = pathlib.Path(__file__).parent / "apple_roots"


class VerificationError(RuntimeError):
    """The payload is not something Apple signed, or not for this app."""


@dataclass(frozen=True)
class AppleTransaction:
    original_transaction_id: str
    product_id: str
    environment: str
    expires_at: datetime | None
    revoked_at: datetime | None
    auto_renew: bool
    app_account_token: str | None = None
    signed_at: datetime | None = None
    transaction_id: str | None = None


@dataclass(frozen=True)
class AppleNotification:
    notification_type: str
    subtype: str | None
    transaction: AppleTransaction | None


def _roots() -> list[bytes]:
    if not _ROOTS_DIR.exists():
        raise VerificationError(
            f"no Apple root certificates at {_ROOTS_DIR} — "
            "download them from apple.com/certificateauthority"
        )
    certs = [p.read_bytes() for p in sorted(_ROOTS_DIR.glob("*.cer"))]
    if not certs:
        raise VerificationError(f"no .cer files in {_ROOTS_DIR}")
    return certs


def _ms_to_dt(value: int | None) -> datetime | None:
    """Apple sends milliseconds since epoch."""
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _verifier(environment: Environment) -> SignedDataVerifier:
    cfg = settings()
    return SignedDataVerifier(
        _roots(),
        # Always on. Turning it off makes the whole exercise decorative.
        enable_online_checks=True,
        environment=environment,
        bundle_id=cfg.apple_iap_bundle_id,
        # Required by the library for Production and rejected as pointless for
        # Sandbox, so it cannot simply be passed in both cases.
        app_apple_id=(
            cfg.apple_app_apple_id if environment == Environment.PRODUCTION else None
        ),
    )


def _both_environments():
    """Sandbox first in non-production.

    TestFlight purchases are sandbox purchases, so a verifier pinned to
    Production rejects every transaction anyone makes before release. The
    environment is part of what is signed, so trying both is not a weakening —
    a sandbox receipt still cannot claim to be a production one.
    """
    if settings().app_env == "prod":
        return (Environment.PRODUCTION, Environment.SANDBOX)
    return (Environment.SANDBOX, Environment.PRODUCTION)


def verify_transaction(signed: str) -> AppleTransaction:
    last: Exception | None = None
    for env in _both_environments():
        try:
            payload = _verifier(env).verify_and_decode_signed_transaction(signed)
            return _to_transaction(payload)
        except (VerificationException, ValueError) as e:
            last = e
    raise VerificationError(f"transaction failed verification: {last}") from last


def verify_notification(signed: str) -> AppleNotification:
    last: Exception | None = None
    for env in _both_environments():
        try:
            payload = _verifier(env).verify_and_decode_notification(signed)
            break
        except (VerificationException, ValueError) as e:
            last = e
    else:
        raise VerificationError(f"notification failed verification: {last}") from last

    data = getattr(payload, "data", None)
    tx = None
    signed_tx = getattr(data, "signedTransactionInfo", None) if data else None
    if signed_tx:
        # The inner transaction is separately signed and gets the same treatment.
        tx = verify_transaction(signed_tx)

    renewal = getattr(data, "signedRenewalInfo", None) if data else None
    if tx is not None and renewal:
        try:
            info = _verifier(Environment(tx.environment)).verify_and_decode_renewal_info(renewal)
            tx = replace(
                tx,
                auto_renew=bool(getattr(info, "autoRenewStatus", 1)),
            )
        except (VerificationException, ValueError):
            # Renewal info is a nice-to-have; the transaction already carries
            # what entitlement needs.
            log.warning("could not decode renewal info; keeping transaction values")

    return AppleNotification(
        notification_type=getattr(getattr(payload, "notificationType", None), "value", ""),
        subtype=getattr(getattr(payload, "subtype", None), "value", None),
        transaction=tx,
    )


def _to_transaction(payload) -> AppleTransaction:
    original = getattr(payload, "originalTransactionId", None)
    product = getattr(payload, "productId", None)
    if not original or not product:
        raise VerificationError("transaction is missing its identifiers")

    allowed = {p.strip() for p in settings().subscription_product_ids.split(",") if p.strip()}
    if product not in allowed:
        # A valid signature for a product we do not sell. Consumables and
        # anything from another of the developer's apps land here.
        raise VerificationError(f"unknown product {product}")

    env = getattr(payload, "environment", None)
    return AppleTransaction(
        original_transaction_id=str(original),
        product_id=str(product),
        environment=getattr(env, "value", None) or str(env or "Production"),
        expires_at=_ms_to_dt(getattr(payload, "expiresDate", None)),
        revoked_at=_ms_to_dt(getattr(payload, "revocationDate", None)),
        auto_renew=True,
        app_account_token=getattr(payload, "appAccountToken", None),
        signed_at=_ms_to_dt(getattr(payload, "signedDate", None)),
        transaction_id=getattr(payload, "transactionId", None),
    )
