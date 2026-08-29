import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DailyBrief(Base):
    __tablename__ = "daily_briefs"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_brief_user_day"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date, index=True)
    readiness_summary: Mapped[str] = mapped_column(Text)
    training_prescription: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # The prescription broken into discrete, tickable items:
    # [{"id": "warmup", "text": "10 min easy to warm up"}, ...]
    # The prose in `training_prescription` stays the thing the athlete reads;
    # this exists so each piece of it can carry a checkbox, which prose cannot.
    session_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # 0-100. The calendar colours each day by this, so it has to be a number the
    # coach committed to rather than something re-derived later — the prose and
    # the colour must never disagree.
    readiness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Whether the coach actually asked for a session. Compliance is meaningless
    # without it: no workout on a prescribed day is a miss, but no workout on a
    # prescribed rest day is exactly right. The prescription is prose and cannot
    # be parsed reliably, so the model emits this alongside it.
    session_planned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # complete | partial | missed | rest. Inferred from logged workouts.
    compliance_auto: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The athlete's correction. Always wins when set — inference is a guess, and
    # being told you skipped a session you did is the worst failure here.
    compliance_override: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Set when the athlete talked Will into changing the session. Kept with the
    # request in their own words, because `_recent_sessions` feeds the last ten
    # days back into tomorrow's brief and "they asked to swap barbell work for
    # dumbbells" is exactly the context that should shape what comes next.
    adjusted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    adjustment_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether last night's sleep had arrived when this was written. False means
    # the brief was produced on a deadline without it, and the day's readiness
    # rests on older data — which is what lets a late sync trigger exactly one
    # regeneration rather than none or an unbounded number.
    had_overnight_sleep: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
