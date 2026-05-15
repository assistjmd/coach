from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import DailyBrief, User

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


class TodayBrief(BaseModel):
    day: date
    readiness_summary: str
    training_prescription: str
    rationale: str | None
    flags: dict | None


@router.get("/today", response_model=TodayBrief)
def today(user: User = Depends(current_user), db: Session = Depends(get_db)) -> TodayBrief:
    brief = (
        db.query(DailyBrief)
        .filter(DailyBrief.user_id == user.id)
        .order_by(DailyBrief.day.desc())
        .first()
    )
    if brief is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no brief yet — nightly job has not run or no data ingested",
        )
    return TodayBrief(
        day=brief.day,
        readiness_summary=brief.readiness_summary,
        training_prescription=brief.training_prescription,
        rationale=brief.rationale,
        flags=brief.flags,
    )
