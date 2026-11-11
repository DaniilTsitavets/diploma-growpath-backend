import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatTurnModel(Base):
    """A stored question and answer; the id is client-generated so a retried POST is idempotent."""

    __tablename__ = "chat_turns"
    __table_args__ = (
        CheckConstraint("outcome IN ('answered', 'not_in_kb', 'out_of_scope')", name="outcome"),
        CheckConstraint("model_effort IN ('medium', 'high')", name="model_effort"),
        CheckConstraint(
            "btrim(question) <> '' AND char_length(question) <= 1000"
            " AND btrim(answer) <> '' AND char_length(answer) <= 4000",
            name="lengths",
        ),
        Index(
            "ix_chat_turns_journal_created", "journal_id", text("created_at DESC"), text("id DESC")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    journal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journals.id"))
    question: Mapped[str]
    outcome: Mapped[str]
    answer: Mapped[str]
    ai_reservation_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    model_effort: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatTurnSourceModel(Base):
    """A citation kept as a snapshot: no foreign key to the section, so reseeding keeps it valid."""

    __tablename__ = "chat_turn_sources"
    __table_args__ = (
        CheckConstraint("rank BETWEEN 1 AND 4", name="rank"),
        CheckConstraint(
            "char_length(document_title) <= 200 AND char_length(section_heading) <= 200"
            " AND char_length(source_url) <= 1000",
            name="lengths",
        ),
    )

    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_turns.id"), primary_key=True)
    rank: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[uuid.UUID]
    document_title: Mapped[str]
    section_heading: Mapped[str]
    source_url: Mapped[str]
