from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.chat import stream_chat
from app.auth.deps import current_user
from app.db import SessionLocal, get_db
from app.models import ChatMessage, User

router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatBody(BaseModel):
    message: str


@router.post("")
def chat(body: ChatBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
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
