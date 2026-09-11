"""Server-side permission to ingest health data and generate coaching."""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import LegalConsent, User

CURRENT_LEGAL_VERSION = "2026-08-05"
MINIMUM_AGE = 16


def age_eligible(user: User) -> bool:
    """Whether we hold a birth date that clears the age gate.

    A missing birth date is not the same as being underage — accounts created
    before the gate existed have none — but it is equally not permission, so
    both answer False here and are told apart by the caller.
    """
    from app.dates import today_for
    born = user.date_of_birth
    if born is None:
        return False
    today = today_for(user)
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age >= MINIMUM_AGE


def accepted_current_documents(db: Session, user: User) -> bool:
    docs = {row[0] for row in db.query(LegalConsent.document).filter(
        LegalConsent.user_id == user.id,
        LegalConsent.version == CURRENT_LEGAL_VERSION,
    )}
    return {"terms", "privacy"} <= docs


def has_consent(db: Session, user: User) -> bool:
    return age_eligible(user) and accepted_current_documents(db, user)


def require_consent(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    """Gate for health ingest, provider sync and anything that coaches.

    The two failures need different words. Someone who accepted the current
    documents but has no birth date on file — every account created before the
    age gate shipped — was being told to accept terms they had already accepted,
    with nothing in the app to tap. `GET /me/consent` reports the same predicate,
    so the existing consent screen re-prompts and collects the missing date.
    """
    if not age_eligible(user):
        raise HTTPException(
            403, "Add your date of birth to confirm you're 16 or over before syncing or coaching."
        )
    if not accepted_current_documents(db, user):
        raise HTTPException(
            403, "Accept the current terms and privacy policy before syncing or coaching."
        )
    return user
