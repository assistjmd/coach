import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DayLog(Base):
    """What the athlete says happened, as opposed to what the data implies.

    Separate from `daily_briefs` rather than more columns on it, because a
    journal entry has to be possible on a day the coach never wrote a brief for —
    a rest day before onboarding, a day the generation failed, a session done
    before they installed the app. Tying the log to the brief would silently drop
    those days.
    """

    __tablename__ = "day_logs"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_day_log_user_day"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date, index=True)

    # The athlete's own account of the day, in their words.
    journal: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ids of the prescribed items they ticked off. Stored as ids rather than
    # indices so that a brief regenerated with a different item order does not
    # silently re-point the ticks at the wrong sessions.
    completed_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # What they actually did, where it differed from the prescription:
    # {"main": {"sets": 4, "reps": 6}}
    #
    # Only the differences are stored. An item done as prescribed has no entry,
    # so "did exactly what was asked" and "recorded nothing" stay distinguishable
    # — which matters, because a coach reading back over the week needs to know
    # whether four sets was a deviation or the plan.
    item_actuals: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
