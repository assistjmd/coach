from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.jwt import decode_jwt
from app.db import get_db
from app.models import User


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        user_id = decode_jwt(token)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from None
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    _touch(db, user)
    return user


# Coarse on purpose. Retention is measured in days, so writing a row on every
# request would cost an UPDATE per call to record nothing new.
_SEEN_RESOLUTION = timedelta(hours=1)


def _touch(db: Session, user: User) -> None:
    now = datetime.now(timezone.utc)
    last = user.last_seen_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or now - last > _SEEN_RESOLUTION:
        user.last_seen_at = now
        db.commit()
