import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import apple, google
from app.auth.jwt import issue_jwt
from app.db import get_db
from app.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class AppleSignInBody(BaseModel):
    identity_token: str
    display_name: str | None = None


class GoogleSignInBody(BaseModel):
    id_token: str


class AuthResponse(BaseModel):
    access_token: str
    user_id: str


@router.post("/apple", response_model=AuthResponse)
def sign_in_with_apple(body: AppleSignInBody, db: Session = Depends(get_db)) -> AuthResponse:
    try:
        claims = apple.verify_identity_token(body.identity_token)
    except Exception as e:
        # The reason goes to the log, not to the caller. This endpoint is
        # unauthenticated, and echoing the exception hands an attacker a
        # description of exactly how their forgery failed — which is how you
        # iterate toward one that does not.
        log.warning("apple sign-in rejected: %s", e)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "that sign-in could not be verified"
        ) from e

    sub = claims["sub"]
    email = claims.get("email")
    user = db.query(User).filter(User.apple_sub == sub).one_or_none()
    if user is None:
        user = User(apple_sub=sub, email=email, display_name=body.display_name)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif email and not user.email:
        user.email = email
        db.commit()

    return AuthResponse(access_token=issue_jwt(user.id), user_id=str(user.id))


@router.post("/google", response_model=AuthResponse)
def sign_in_with_google(body: GoogleSignInBody, db: Session = Depends(get_db)) -> AuthResponse:
    try:
        claims = google.verify_id_token(body.id_token)
    except Exception as e:
        log.warning("google sign-in rejected: %s", e)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "that sign-in could not be verified"
        ) from e

    sub = claims["sub"]
    email = claims.get("email")
    name = claims.get("name")
    user = db.query(User).filter(User.google_sub == sub).one_or_none()
    if user is None:
        user = User(google_sub=sub, email=email, display_name=name)
        db.add(user)
        db.commit()
        db.refresh(user)

    return AuthResponse(access_token=issue_jwt(user.id), user_id=str(user.id))
