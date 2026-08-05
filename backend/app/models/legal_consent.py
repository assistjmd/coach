import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LegalConsent(Base):
    """A record that someone accepted a specific version of a document.

    Append-only, and deliberately not a pair of columns on `users`. If the terms
    change, the question that matters later is "which version did this person
    accept, and when" — and overwriting a single field destroys exactly that
    answer. Rows accumulate; the newest per (user, document) is the current one.

    Not cascade-deleted with the user on purpose is tempting, but wrong: the
    consent record is personal data too, and keeping it after an erasure request
    would defeat the erasure. It cascades.
    """

    __tablename__ = "legal_consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # "terms" | "privacy"
    document: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
