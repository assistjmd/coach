import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import LegalConsent, OAuthToken, User
from app.providers import all_providers

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["me"])


class ConnectedProvider(BaseModel):
    slug: str
    display_name: str
    connected: bool
    last_synced_at: datetime | None
    # Why the last sync failed, if it did. The app shows this so a revoked
    # authorization reads as a revoked authorization rather than as no new data.
    last_sync_error: str | None = None


class MeResponse(BaseModel):
    id: str
    email: str | None
    display_name: str | None
    timezone: str
    onboarded: bool
    providers: list[ConnectedProvider]


class OnboardingComplete(BaseModel):
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone: {v}") from e
        return v


def _me(user: User, db: Session) -> MeResponse:
    tokens = {t.provider: t for t in db.query(OAuthToken).filter(OAuthToken.user_id == user.id)}
    providers = [
        ConnectedProvider(
            slug=p.info.slug,
            display_name=p.info.display_name,
            connected=(p.info.slug in tokens) or p.info.auth_kind == "device_push",
            last_synced_at=tokens[p.info.slug].last_synced_at if p.info.slug in tokens else None,
            last_sync_error=tokens[p.info.slug].last_sync_error if p.info.slug in tokens else None,
        )
        for p in all_providers()
    ]
    return MeResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        timezone=user.timezone,
        onboarded=user.onboarded_at is not None,
        providers=providers,
    )


class TimezoneBody(BaseModel):
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone: {v}") from e
        return v


@router.put("/me/timezone", response_model=MeResponse)
def set_timezone(
    body: TimezoneBody,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Keep the stored timezone matching the device.

    It was previously written once, at onboarding, and never again — so someone
    who moved, or who onboarded while travelling, kept getting their brief at the
    wrong local hour forever with no way to correct it.
    """
    if user.timezone != body.timezone:
        log.info("timezone changed for user %s: %s -> %s",
                 user.id, user.timezone, body.timezone)
        user.timezone = body.timezone
        db.commit()
        db.refresh(user)
    return _me(user, db)


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> MeResponse:
    return _me(user, db)


@router.post("/me/onboarding/complete", response_model=MeResponse)
def complete_onboarding(
    body: OnboardingComplete,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Mark onboarding finished and record the device's timezone.

    Timezone is captured here because this is the only moment we reliably hear
    from the device. `users.timezone` defaults to UTC and nothing else ever wrote
    it, yet it is fed to the model as context and is what the daily-brief
    schedule should key off — a Sydney athlete getting a "morning" brief at 9pm
    is the symptom.
    """
    if user.onboarded_at is None:
        user.onboarded_at = datetime.now(timezone.utc)
    user.timezone = body.timezone
    db.commit()
    db.refresh(user)
    return _me(user, db)


@router.delete("/me/onboarding", status_code=status.HTTP_204_NO_CONTENT)
def reset_onboarding(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    """Re-run onboarding on next launch. Dev/support affordance."""
    from app.config import settings

    if settings().app_env != "dev":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    user.onboarded_at = None
    db.commit()


# Bumped when either document changes materially. The app compares this against
# what the user last accepted and re-prompts on a mismatch, so a silent edit to
# the terms does not quietly count as agreed.
CURRENT_LEGAL_VERSION = "2026-08-05"


class ConsentBody(BaseModel):
    version: str


class ConsentStatus(BaseModel):
    current_version: str
    accepted_version: str | None
    accepted_at: datetime | None
    up_to_date: bool


@router.get("/me/consent", response_model=ConsentStatus)
def consent_status(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> ConsentStatus:
    latest = (
        db.query(LegalConsent)
        .filter(LegalConsent.user_id == user.id, LegalConsent.document == "terms")
        .order_by(LegalConsent.accepted_at.desc())
        .first()
    )
    return ConsentStatus(
        current_version=CURRENT_LEGAL_VERSION,
        accepted_version=latest.version if latest else None,
        accepted_at=latest.accepted_at if latest else None,
        up_to_date=bool(latest and latest.version == CURRENT_LEGAL_VERSION),
    )


@router.post("/me/consent", response_model=ConsentStatus, status_code=status.HTTP_201_CREATED)
def record_consent(
    body: ConsentBody,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ConsentStatus:
    """Record acceptance of the terms and privacy policy.

    Written as two rows, one per document, because they are separate agreements
    and a regulator asking "did they accept the privacy policy" should not have
    to infer it from a row labelled "terms".
    """
    if body.version != CURRENT_LEGAL_VERSION:
        # Refuse to record consent to a version that is not the live one. A stale
        # client would otherwise write a record that looks like agreement to
        # documents the user never saw.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"stale version {body.version}; current is {CURRENT_LEGAL_VERSION}",
        )
    for document in ("terms", "privacy"):
        db.add(LegalConsent(user_id=user.id, document=document, version=body.version))
    db.commit()
    return consent_status(user=user, db=db)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    """Permanently delete the account and everything attached to it.

    Required by App Store guideline 5.1.1(v): an app that creates accounts must
    let you delete one from inside the app, not by emailing support.

    Every table referencing `users.id` declares ON DELETE CASCADE, so this single
    delete removes goals, briefs, chat history, workouts, sleep, HRV, body
    metrics, provider tokens and consent records with it. Deliberately not a soft
    delete: an erasure request under GDPR or CCPA means the data is gone, and a
    flagged row that still holds health data does not satisfy that.
    """
    db.delete(user)
    db.commit()
