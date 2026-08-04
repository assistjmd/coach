from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import Goal, User
from app.providers.sports import Sport

router = APIRouter(prefix="/v1/goal", tags=["goal"])

GoalKind = Literal["race", "improvement", "adaptive"]


class GoalBody(BaseModel):
    # Enumerated rather than free strings: onboarding is about to start writing
    # these for every user, and `sport` shares its vocabulary with the workout
    # data the coach reasons over. Pinning it now is far cheaper than migrating
    # inconsistent rows later.
    kind: GoalKind
    sport: Sport | None = None
    target_date: date | None = None
    description: str | None = None
    params: dict | None = None

    @model_validator(mode="after")
    def _race_needs_a_date(self):
        if self.kind == "race" and self.target_date is None:
            raise ValueError("target_date is required when kind is 'race'")
        return self


class GoalResponse(GoalBody):
    pass


def _as_response(g: Goal) -> GoalResponse:
    return GoalResponse(
        kind=g.kind,
        sport=g.sport,
        target_date=g.target_date,
        description=g.description,
        params=g.params,
    )


@router.get("", response_model=GoalResponse | None)
def get_goal(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> GoalResponse | None:
    g = db.query(Goal).filter(Goal.user_id == user.id).one_or_none()
    return None if g is None else _as_response(g)


@router.put("", response_model=GoalResponse)
def put_goal(
    body: GoalBody, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> GoalResponse:
    g = db.query(Goal).filter(Goal.user_id == user.id).one_or_none()
    if g is None:
        g = Goal(user_id=user.id, kind=body.kind)
        db.add(g)
    g.kind = body.kind
    g.sport = body.sport
    g.target_date = body.target_date
    g.description = body.description
    g.params = body.params
    db.commit()
    db.refresh(g)
    # Return the stored row, not the request body: the client should see what was
    # actually persisted rather than an echo of what it sent.
    return _as_response(g)
