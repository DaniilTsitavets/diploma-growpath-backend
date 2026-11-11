import uuid

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models import EscalationModel, JournalModel, UpdateModel
from app.service.model import Escalation, EscalationStatus, NewEscalation


def escalation_select() -> Select:
    """Escalations with the journal and the user their update belongs to."""
    return (
        select(
            EscalationModel.id,
            EscalationModel.update_id,
            UpdateModel.journal_id,
            JournalModel.user_id,
            EscalationModel.status,
            EscalationModel.ticket_id,
            EscalationModel.user_question,
            EscalationModel.answer_text,
            EscalationModel.answered_at,
            EscalationModel.created_at,
        )
        .join(UpdateModel, UpdateModel.id == EscalationModel.update_id)
        .join(JournalModel, JournalModel.id == UpdateModel.journal_id)
    )


def to_escalation(row: Row) -> Escalation:
    fields = row._mapping
    return Escalation(**{**fields, "status": EscalationStatus(fields["status"])})


class SqlAlchemyEscalationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, escalation: NewEscalation) -> bool:
        result = self._session.execute(
            insert(EscalationModel)
            .values(
                id=escalation.id,
                update_id=escalation.update_id,
                user_question=escalation.user_question,
            )
            .on_conflict_do_nothing(
                index_elements=[EscalationModel.update_id],
                index_where=text("status <> 'cancelled'"),
            )
            .returning(EscalationModel.id)
        )
        return result.scalar_one_or_none() is not None

    def get(self, escalation_id: uuid.UUID) -> Escalation | None:
        return self._one(escalation_select().where(EscalationModel.id == escalation_id))

    def get_active(self, update_id: uuid.UUID) -> Escalation | None:
        return self._one(
            escalation_select().where(
                EscalationModel.update_id == update_id, EscalationModel.status != "cancelled"
            )
        )

    def latest(self, update_id: uuid.UUID) -> Escalation | None:
        return self._one(
            escalation_select()
            .where(EscalationModel.update_id == update_id)
            .order_by(EscalationModel.created_at.desc())
            .limit(1)
        )

    def _one(self, statement: Select) -> Escalation | None:
        row = self._session.execute(statement).one_or_none()
        return None if row is None else to_escalation(row)
