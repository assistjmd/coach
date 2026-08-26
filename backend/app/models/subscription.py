import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Subscription(Base):
    """One row per Apple original transaction.

    Keyed on `original_transaction_id` rather than the per-renewal transaction
    id: Apple issues a new transaction every renewal, all sharing the original.
    Keying on the renewal would grow a row a month and make "is this person
    subscribed" a question about the newest of many.

    Entitlement is decided from `expires_at` and `revoked`, never from anything
    the client asserts. The app tells us a purchase happened; Apple's signature
    is what makes it true.
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("original_transaction_id", name="uq_subscription_original_tx"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    original_transaction_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(128))
    # "Sandbox" during TestFlight, "Production" after release. Kept so a sandbox
    # subscription can never be mistaken for a paying customer in any count.
    environment: Mapped[str] = mapped_column(String(16), default="Production")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Refunded or revoked by Apple. Separate from expiry because a refund can
    # arrive while the period is still notionally live, and it must end access
    # immediately rather than at the next renewal date.
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
