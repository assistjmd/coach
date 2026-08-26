from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.chat import stream_chat
from app.auth.deps import current_user
from app.config import settings
from app.db import SessionLocal, get_db
from app.entitlement import require as paid
from app.quota import enforce, limit_for, used_recently
from app.models import ChatMessage, User

router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatBody(BaseModel):
    # Bounded because the daily count is not the only cost that matters: 25
    # unbounded messages can be 25 megabytes of input tokens. Two thousand
    # characters is far more than anyone types at a coach.
    message: str = Field(min_length=1, max_length=2000)


class QuotaResponse(BaseModel):
    used: int
    limit: int
    remaining: int
    # Session adjustment draws on its own, smaller allowance.
    adjust_used: int = 0
    adjust_limit: int = 0
    adjust_remaining: int = 0


@router.get("/quota", response_model=QuotaResponse)
def quota(user: User = Depends(current_user), db: Session = Depends(get_db)) -> QuotaResponse:
    """Lets the client show what's left before the user walks into the wall."""
    limit = limit_for("chat")
    used = used_recently(db, user, "chat")
    adjust_limit = limit_for("adjust")
    adjust_used = used_recently(db, user, "adjust")
    return QuotaResponse(
        used=used,
        limit=limit,
        remaining=max(0, limit - used),
        adjust_used=adjust_used,
        adjust_limit=adjust_limit,
        adjust_remaining=max(0, adjust_limit - adjust_used),
    )


@router.post("")
def chat(body: ChatBody, user: User = Depends(paid), db: Session = Depends(get_db)):
    # Session adjustment has its own allowance — see app/quota.py.
    enforce(db, user, kind="chat")

    db.add(ChatMessage(user_id=user.id, role="user", content=body.message))
    db.commit()

    def event_stream():
        chunks: list[str] = []
        for delta in stream_chat(db, user, body.message):
            chunks.append(delta)
            yield f"data: {delta}\n\n"
        full = "".join(chunks)
        # Fresh session: the request-scoped `db` is closed once streaming starts.
        with SessionLocal() as s:
            s.add(ChatMessage(user_id=user.id, role="assistant", content=full))
            s.commit()
        yield "event: done\ndata: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
