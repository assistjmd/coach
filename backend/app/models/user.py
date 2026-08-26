import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    apple_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # The terms require 16+. Stored as a date rather than a "confirmed 16+"
    # boolean because a checkbox records only that someone tapped yes, and
    # because the value is also what lets a future brief reason about age.
    # Nullable: accounts created before the gate existed have no answer, and
    # inventing one would be worse than an honest null.
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    # When the free trial ends. Set at account creation and never extended, so
    # deleting and recreating an account is the only way to get a second one —
    # and that costs the athlete all their history, which is a fair price.
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Server-side rather than a client flag: a local flag would re-run onboarding
    # after a reinstall or on a second device, for a user who already has a goal.
    onboarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
