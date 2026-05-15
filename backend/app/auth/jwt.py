import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.config import settings


def issue_jwt(user_id: uuid.UUID) -> str:
    cfg = settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=cfg.jwt_expires_hours)).timestamp()),
        "iss": "coach",
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_alg)


def decode_jwt(token: str) -> uuid.UUID:
    cfg = settings()
    try:
        payload = jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_alg])
    except JWTError as e:
        raise ValueError("invalid token") from e
    return uuid.UUID(payload["sub"])
