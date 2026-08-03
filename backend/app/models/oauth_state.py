import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class OAuthStateUse(Base):
    """Consumed OAuth state `jti`s — the single-use half of state validation.

    The state token itself is a signed JWT (see `app/auth/jwt.py`), so it is
    stateless and multi-worker safe for authenticity and CSRF binding. What a
    JWT cannot express is "only once", so the callback records each `jti` here
    via INSERT ... ON CONFLICT DO NOTHING; losing the race means replay.

    Written only on the success path, and purged after a day by `sync_all`, so
    this table stays tiny. There is deliberately no row at *issue* time — that
    would put a write on every Connect tap and recreate the state store we are
    removing.
    """

    __tablename__ = "oauth_state_uses"

    jti: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # No FK to users: this is an anti-replay ledger, and it must survive
    # (and keep rejecting) even if the user row is deleted mid-flow.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
