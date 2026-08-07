"""Data export.

The privacy policy promises a copy of everything on request, which under GDPR
Article 20 and the CCPA means machine-readable and complete. Until now it
promised something that did not exist.

Two decisions worth stating:

Everything is built in one pass and streamed as a single JSON document rather
than paged. An export the user has to reassemble from pages is not portability,
it is homework — and at one person's training history the whole thing is a few
megabytes.

Secrets are excluded, deliberately and specifically. OAuth access and refresh
tokens are *about* the user but handing them over would put live credentials to
their Strava and Oura accounts in a file that ends up in a Downloads folder. The
connections are listed by name and date so nothing is hidden; the keys are not.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import (
    BodyMetric,
    ChatMessage,
    DailyBrief,
    DayLog,
    Goal,
    HeartRateSample,
    HRVReading,
    LegalConsent,
    OAuthToken,
    ReadinessScore,
    SleepSession,
    User,
    Workout,
)

router = APIRouter(prefix="/v1", tags=["me"])

# Never exported. `access_token` and `refresh_token` are live credentials to the
# user's accounts at other services; `id` values are internal surrogate keys with
# no meaning outside this database.
_REDACTED = {
    "access_token",
    "refresh_token",
    # The at-rest column names. Listed too so that routing OAuthToken through
    # `_rows` later cannot quietly start exporting ciphertext.
    "access_token_enc",
    "refresh_token_enc",
    "user_id",
    "id",
}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _rows(db: Session, model, user_id, order_by=None) -> list[dict[str, Any]]:
    """Every column of every row for one user, minus the redacted set."""
    q = db.query(model).filter(model.user_id == user_id)
    if order_by is not None:
        q = q.order_by(order_by)
    return [
        {
            c.name: _serialize(getattr(row, c.name))
            for c in model.__table__.columns
            if c.name not in _REDACTED
        }
        for row in q
    ]


@router.get("/me/export")
def export_everything(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """A complete, machine-readable copy of this account.

    Served as a download rather than a rendered body so that tapping it in the
    app produces a file the user can keep, mail, or open in something else —
    which is the entire point of a portability right.
    """
    payload = {
        "export_version": 1,
        "exported_at": datetime.now().astimezone().isoformat(),
        "account": {
            "email": user.email,
            "display_name": user.display_name,
            "timezone": user.timezone,
            "date_of_birth": _serialize(getattr(user, "date_of_birth", None)),
            "created_at": _serialize(user.created_at),
            "onboarded_at": _serialize(user.onboarded_at),
        },
        # Named and dated, without the keys. Concealing that a connection exists
        # would defeat the purpose; including its credentials would be reckless.
        "connections": [
            {
                "provider": t.provider,
                "connected_at": _serialize(t.created_at),
                "last_synced_at": _serialize(t.last_synced_at),
                "scope": t.scope,
            }
            for t in db.query(OAuthToken).filter(OAuthToken.user_id == user.id)
        ],
        "consents": _rows(db, LegalConsent, user.id),
        "goals": _rows(db, Goal, user.id),
        "briefs": _rows(db, DailyBrief, user.id, DailyBrief.day),
        "day_logs": _rows(db, DayLog, user.id, DayLog.day),
        "workouts": _rows(db, Workout, user.id, Workout.started_at),
        "sleep": _rows(db, SleepSession, user.id, SleepSession.started_at),
        "hrv": _rows(db, HRVReading, user.id, HRVReading.measured_at),
        "heart_rate": _rows(db, HeartRateSample, user.id, HeartRateSample.measured_at),
        "readiness": _rows(db, ReadinessScore, user.id, ReadinessScore.day),
        "body_metrics": _rows(db, BodyMetric, user.id, BodyMetric.measured_at),
        "chat": _rows(db, ChatMessage, user.id, ChatMessage.created_at),
    }

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d")
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="coach-will-export-{stamp}.json"'
        },
    )
