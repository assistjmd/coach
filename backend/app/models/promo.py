import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PromoCode(Base):
    """A code that extends someone's free access.

    A table rather than a config list so a code can be retired, capped or dated
    without a deploy — the point of a promo code is to hand it out quickly and
    stop it quickly, and neither should need a release.

    Codes are stored and compared uppercase. Someone typing one on a phone
    keyboard will get the case wrong and should not be told they are wrong.
    """

    __tablename__ = "promo_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    extra_days: Mapped[int] = mapped_column(Integer, default=30)
    # Null means unlimited. A cap is what stops a code posted publicly from
    # becoming a permanent free tier.
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromoRedemption(Base):
    """One person's use of one code.

    The unique constraint is the enforcement: a code cannot be redeemed twice by
    the same account, so nobody extends their trial indefinitely by retyping it.
    """

    __tablename__ = "promo_redemptions"
    __table_args__ = (
        UniqueConstraint("user_id", "promo_code_id", name="uq_promo_once_per_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    promo_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("promo_codes.id", ondelete="CASCADE"), index=True
    )
    # What the trial was extended to, kept so a support question can be answered
    # from the record rather than reconstructed.
    extended_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
