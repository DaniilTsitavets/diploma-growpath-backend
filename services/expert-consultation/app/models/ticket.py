import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TicketModel(Base):
    """A question for an expert; `open` until it is answered or expires."""

    __tablename__ = "tickets"
    __table_args__ = (
        # A coarse safety net: `jsonb::text` is longer than the compact form the 64 KB limit
        # is measured on, so the exact limit is checked when the event is accepted.
        CheckConstraint(
            "jsonb_typeof(context) = 'object' AND octet_length(context::text) <= 131072",
            name="context",
        ),
        # An OR of conjunctions, so an unknown status cannot slip through a NULL.
        CheckConstraint(
            "(status = 'open' AND answered_at IS NULL AND cancelled_at IS NULL)"
            " OR (status = 'answered' AND answered_at IS NOT NULL AND cancelled_at IS NULL)"
            " OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND answered_at IS NULL)",
            name="state",
        ),
        CheckConstraint(
            "expires_at > created_at"
            " AND (answered_at IS NULL"
            " OR (answered_at >= created_at AND answered_at <= expires_at))"
            " AND (cancelled_at IS NULL OR cancelled_at >= expires_at)",
            name="times",
        ),
        CheckConstraint(
            "status <> 'open' OR terminal_event_published_at IS NULL", name="event_published"
        ),
        Index(
            "uq_tickets_active_update",
            "update_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index(
            "ix_tickets_open_expires",
            "expires_at",
            "id",
            postgresql_where=text("status = 'open'"),
        ),
        Index(
            "ix_tickets_unpublished",
            "id",
            postgresql_where=text("status <> 'open' AND terminal_event_published_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    escalation_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    user_id: Mapped[uuid.UUID]
    journal_id: Mapped[uuid.UUID]
    update_id: Mapped[uuid.UUID]
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_event_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnswerModel(Base):
    """The expert's answer; its primary key is the ticket, so a ticket has at most one."""

    __tablename__ = "answers"
    __table_args__ = (
        # Spaces, tabs and line breaks do not count as text.
        CheckConstraint(
            r"btrim(body, E' \t\r\n') <> '' AND char_length(body) <= 5000", name="body"
        ),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), primary_key=True
    )
    expert_id: Mapped[uuid.UUID]
    body: Mapped[str]
