"""Server-side permission to ingest health data and generate coaching."""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import LegalConsent, User

CURRENT_LEGAL_VERSION = "2026-08-05"
MINIMUM_AGE = 16


def has_consent(db: Session, user: User) -> bool:
    from app.dates import today_for
    born = user.date_of_birth
    if born is None:
        return False
    today = today_for(user)
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if age < MINIMUM_AGE:
        return False
    docs = {row[0] for row in db.query(LegalConsent.document).filter(
        LegalConsent.user_id == user.id,
        LegalConsent.version == CURRENT_LEGAL_VERSION,
    )}
    return {"terms", "privacy"} <= docs


def require_consent(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if not has_consent(db, user):
        raise HTTPException(403, "Accept the current terms and privacy policy before syncing or coaching.")
    return user
