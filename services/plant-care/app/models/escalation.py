import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EscalationModel(Base):
    """A request for an expert; `requested` means no ticket has confirmed it yet."""

    __tablename__ = "escalations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'ticket_open', 'answered', 'cancelled')", name="status"
        ),
        CheckConstraint(
            "char_length(user_question) <= 1000 AND char_length(answer_text) <= 5000",
            name="lengths",
        ),
        CheckConstraint("status <> 'ticket_open' OR ticket_id IS NOT NULL", name="ticket_open"),
        CheckConstraint(
            "(status = 'answered')"
            " = (answer_text IS NOT NULL AND answered_at IS NOT NULL AND ticket_id IS NOT NULL)",
            name="answered",
        ),
        Index(
            "uq_escalations_active_update",
            "update_id",
            unique=True,
            postgresql_where=text("status <> 'cancelled'"),
        ),
        Index("ix_escalations_update_created", "update_id", text("created_at DESC")),
        Index(
            "ix_escalations_requested",
            "created_at",
            postgresql_where=text("status = 'requested'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    update_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("updates.id"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(server_default="requested")
    user_question: Mapped[str | None]
    answer_text: Mapped[str | None]
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
