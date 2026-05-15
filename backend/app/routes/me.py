from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
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
    providers: list[ConnectedProvider]


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> MeResponse:
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
        providers=providers,
    )
