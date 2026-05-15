from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.db import get_db
from app.models import Goal, User

router = APIRouter(prefix="/v1/goal", tags=["goal"])


class GoalBody(BaseModel):
    kind: str  # race | improvement | adaptive
    sport: str | None = None
    target_date: date | None = None
    description: str | None = None
    params: dict | None = None


class GoalResponse(GoalBody):
    pass


@router.get("", response_model=GoalResponse | None)
def get_goal(user: User = Depends(current_user), db: Session = Depends(get_db)) -> GoalResponse | None:
    g = db.query(Goal).filter(Goal.user_id == user.id).one_or_none()
    if g is None:
        return None
    return GoalResponse(
        kind=g.kind,
        sport=g.sport,
        target_date=g.target_date,
        description=g.description,
        params=g.params,
    )


@router.put("", response_model=GoalResponse)
def put_goal(body: GoalBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> GoalResponse:
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
    return body
