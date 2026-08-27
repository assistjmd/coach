from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
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


class HistoryMessage(BaseModel):
    role: str
    content: str
    created_at: datetime


class HistoryResponse(BaseModel):
    messages: list[HistoryMessage]
    # True when older messages exist beyond what was returned, so the client
    # knows whether scrolling further up is worth offering.
    has_more: bool


@router.get("/history", response_model=HistoryResponse)
def history(
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> HistoryResponse:
    """The conversation, newest first, paged backwards.

    Every message has been stored since chat existed — for the data export and
    for metering — and the app simply never asked for it, so the transcript
    vanished the moment you left the tab.

    Adjustment exchanges are excluded. They live in the same table so they can
    be metered and exported, but they are a different conversation: replaying
    "no barbell today" in the chat transcript would read as something the
    athlete said to the coach here.
    """
    q = db.query(ChatMessage).filter(
        ChatMessage.user_id == user.id, ChatMessage.kind == "chat"
    )
    if before is not None:
        q = q.filter(ChatMessage.created_at < before)

    # One extra row is the cheapest way to know whether more exist.
    rows = q.order_by(ChatMessage.created_at.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    return HistoryResponse(
        # Reversed so the caller receives them oldest-first and can append
        # rather than reorder.
        messages=[
            HistoryMessage(role=m.role, content=m.content, created_at=m.created_at)
            for m in reversed(rows)
        ],
        has_more=has_more,
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
