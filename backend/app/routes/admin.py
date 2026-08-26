"""Aggregate product metrics, for the developer.

Gated on an email allowlist rather than a shared admin token: there is one
operator, he already authenticates, and a second secret is a second thing to
leak. An empty allowlist closes the route entirely, which is the right default
for any environment nobody thought to configure.

Everything served here is counts and rates. No message body, journal entry or
prescription is ever selected — see the note in `analytics/product.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.analytics.product import collect
from app.auth.deps import current_user
from app.config import settings
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _admin(user: User = Depends(current_user)) -> User:
    allowed = {
        e.strip().lower() for e in settings().admin_emails.split(",") if e.strip()
    }
    if not allowed or (user.email or "").lower() not in allowed:
        # 404 rather than 403: an unauthorised caller learns nothing about
        # whether this route exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return user


@router.get("/metrics")
def metrics(_: User = Depends(_admin), db: Session = Depends(get_db)) -> dict:
    return collect(db).as_dict()
