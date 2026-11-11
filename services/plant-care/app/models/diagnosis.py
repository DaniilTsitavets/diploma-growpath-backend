import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DiagnosisModel(Base):
    """At most one per update; its status moves only through guarded UPDATE statements."""

    __tablename__ = "diagnoses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('diagnosis_pending', 'awaiting_clarification', 'completed', 'degraded')",
            name="status",
        ),
        CheckConstraint("model_effort IN ('medium', 'high')", name="model_effort"),
        CheckConstraint(
            "char_length(clarifying_question) <= 500 AND char_length(clarifying_answer) <= 1000"
            " AND char_length(recommendation) <= 8000",
            name="lengths",
        ),
        CheckConstraint(
            "clarifying_answer IS NULL OR clarifying_question IS NOT NULL",
            name="answer_needs_question",
        ),
        CheckConstraint(
            "status <> 'awaiting_clarification'"
            " OR (clarifying_question IS NOT NULL AND clarifying_answer IS NULL)",
            name="awaiting",
        ),
        CheckConstraint(
            "NOT roots_photo_requested OR clarifying_question IS NOT NULL", name="roots_photo"
        ),
        CheckConstraint(
            "status = 'diagnosis_pending' OR model_effort IS NOT NULL", name="model_effort_set"
        ),
        CheckConstraint(
            "status NOT IN ('diagnosis_pending', 'awaiting_clarification')"
            " OR recommendation IS NULL",
            name="no_recommendation_yet",
        ),
        CheckConstraint(
            "(status <> 'completed' OR (recommendation IS NOT NULL"
            " AND has_dominant_candidate IS NOT NULL))"
            " AND (status <> 'degraded' OR recommendation IS NOT NULL)",
            name="finished",
        ),
        CheckConstraint(
            "NOT escalation_offered"
            " OR (clarifying_answer IS NOT NULL AND has_dominant_candidate IS FALSE)",
            name="escalation_offered",
        ),
        CheckConstraint(
            "NOT escalation_declined OR escalation_offered", name="escalation_declined"
        ),
    )

    update_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("updates.id"), primary_key=True)
    ai_reservation_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    status: Mapped[str]
    model_effort: Mapped[str | None]
    has_dominant_candidate: Mapped[bool | None]
    clarifying_question: Mapped[str | None]
    clarifying_answer: Mapped[str | None]
    roots_photo_requested: Mapped[bool] = mapped_column(server_default=text("false"))
    recommendation: Mapped[str | None]
    escalation_offered: Mapped[bool] = mapped_column(server_default=text("false"))
    escalation_declined: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DiagnosisCandidateModel(Base):
    __tablename__ = "diagnosis_candidates"
    __table_args__ = (
        CheckConstraint("rank >= 1", name="rank"),
        CheckConstraint(
            "char_length(cause) <= 200 AND char_length(rationale) <= 1000", name="lengths"
        ),
    )

    update_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diagnoses.update_id"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(primary_key=True)
    cause: Mapped[str]
    rationale: Mapped[str]
