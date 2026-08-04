from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import OAuthToken, User
from app.providers import all_providers

router = APIRouter(prefix="/v1", tags=["me"])


class ConnectedProvider(BaseModel):
    slug: str
    display_name: str
    connected: bool
    last_synced_at: datetime | None


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
