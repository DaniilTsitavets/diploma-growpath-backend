import uuid
from datetime import datetime

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.models import EscalationModel
from app.service.model import EscalationStatus

REQUESTED = EscalationStatus.REQUESTED.value
TICKET_OPEN = EscalationStatus.TICKET_OPEN.value
UNFINISHED = (REQUESTED, TICKET_OPEN)


class SqlAlchemyTicketEventRepository:
    """Guarded updates of an escalation by the events of its ticket, in any order."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def mark_ticket_open(self, escalation_id: uuid.UUID, ticket_id: uuid.UUID) -> bool:
        return self._apply(
            escalation_id, ticket_id, (REQUESTED,), status=EscalationStatus.TICKET_OPEN.value
        )

    def mark_answered(
        self,
        escalation_id: uuid.UUID,
        ticket_id: uuid.UUID,
        answer_text: str,
        answered_at: datetime,
    ) -> bool:
        return self._apply(
            escalation_id,
            ticket_id,
            UNFINISHED,
            status=EscalationStatus.ANSWERED.value,
            answer_text=answer_text,
            answered_at=answered_at,
        )

    def mark_cancelled(self, escalation_id: uuid.UUID, ticket_id: uuid.UUID) -> bool:
        return self._apply(
            escalation_id, ticket_id, UNFINISHED, status=EscalationStatus.CANCELLED.value
        )

    def _apply(
        self,
        escalation_id: uuid.UUID,
        ticket_id: uuid.UUID,
        from_statuses: tuple[str, ...],
        **values: object,
    ) -> bool:
        result = self._session.execute(
            update(EscalationModel)
            .where(
                EscalationModel.id == escalation_id,
                EscalationModel.status.in_(from_statuses),
                or_(EscalationModel.ticket_id.is_(None), EscalationModel.ticket_id == ticket_id),
            )
            .values(ticket_id=ticket_id, **values)
            .returning(EscalationModel.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None
