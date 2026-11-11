import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, DateTime, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class JournalModel(Base):
    """One journal per pack order; `planted_on` NULL means the plant is not planted yet."""

    __tablename__ = "journals"
    __table_args__ = (
        UniqueConstraint("order_id"),
        CheckConstraint("btrim(species) <> '' AND char_length(species) <= 100", name="species"),
        Index("ix_journals_user_created", "user_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID]
    order_id: Mapped[uuid.UUID]
    species: Mapped[str]
    planted_on: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
