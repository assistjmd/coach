from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.chat import stream_chat
from app.auth.deps import current_user
from app.config import settings
from app.db import SessionLocal, get_db
from app.models import ChatMessage, User

router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatBody(BaseModel):
    message: str


class QuotaResponse(BaseModel):
    used: int
    limit: int
    remaining: int


def _used_recently(db: Session, user: User) -> int:
    """Messages this user has sent in the last 24 hours.

    A rolling window rather than a calendar day: a calendar reset lets someone
    spend a whole allowance at 23:59 and another at 00:01, which is exactly the
    burst this exists to prevent. It also avoids needing the user's timezone to
    decide when "today" starts.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return (
        db.query(func.count(ChatMessage.id))
        .filter(
            ChatMessage.user_id == user.id,
            ChatMessage.role == "user",
            ChatMessage.created_at >= since,
        )
        .scalar()
        or 0
    )


@router.get("/quota", response_model=QuotaResponse)
def quota(user: User = Depends(current_user), db: Session = Depends(get_db)) -> QuotaResponse:
    """Lets the client show what's left before the user walks into the wall."""
    limit = settings().chat_daily_message_limit
    used = _used_recently(db, user)
    return QuotaResponse(used=used, limit=limit, remaining=max(0, limit - used))


@router.post("")
def chat(body: ChatBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    limit = settings().chat_daily_message_limit
    used = _used_recently(db, user)
    if used >= limit:
        # 429 rather than 403: it is a rate limit, and the client should say when
        # it resets rather than presenting it as a permissions problem.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"That's all {limit} questions for today. More in a few hours.",
        )

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
