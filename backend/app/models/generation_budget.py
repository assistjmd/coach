from datetime import date, datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GenerationBudget(Base):
    """Durable attempts and a short lease for each athlete's morning brief."""
    __tablename__ = "generation_budgets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
