import uuid
from datetime import datetime

from app.service.model import EscalationStatus
from tests.unit.escalation_fakes import EscalationUnitOfWork, FakeEscalations

REQUESTED = EscalationStatus.REQUESTED
TICKET_OPEN = EscalationStatus.TICKET_OPEN


class FakeTicketEvents:
    """Mirrors the guarded UPDATE statements of the SQL repository, including both checks."""

    def __init__(self, escalations: FakeEscalations) -> None:
        self._escalations = escalations

    def mark_ticket_open(self, escalation_id: uuid.UUID, ticket_id: uuid.UUID) -> bool:
        return self._apply(
            escalation_id, ticket_id, (REQUESTED,), status=EscalationStatus.TICKET_OPEN
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
            (REQUESTED, TICKET_OPEN),
            status=EscalationStatus.ANSWERED,
            answer_text=answer_text,
            answered_at=answered_at,
        )

    def mark_cancelled(self, escalation_id: uuid.UUID, ticket_id: uuid.UUID) -> bool:
        return self._apply(
            escalation_id, ticket_id, (REQUESTED, TICKET_OPEN), status=EscalationStatus.CANCELLED
        )

    def _apply(self, escalation_id, ticket_id, from_statuses, **values) -> bool:
        row = self._escalations.rows.get(escalation_id)
        if row is None or row.status not in from_statuses:
            return False
        if row.ticket_id is not None and row.ticket_id != ticket_id:
            return False
        self._escalations.change(escalation_id, ticket_id=ticket_id, **values)
        return True


class TicketUnitOfWork(EscalationUnitOfWork):
    def __init__(self) -> None:
        super().__init__()
        self.ticket_events = FakeTicketEvents(self.escalations)
