import logging
import uuid
from collections.abc import Callable

from app.service.ports import UnitOfWork
from app.service.ticket_events import TicketAnswered, TicketCancelled, TicketCreated

logger = logging.getLogger(__name__)


class TicketEventHandlers:
    """Idempotent reactions to the ticket events; any order and any repetition is tolerated.

    Every handler stores the ticket id and moves the escalation only from a state the event may
    follow, so `ticket.answered` arriving before `ticket.created` still lands. No rows changed
    means a duplicate or a late event: it is logged and acknowledged. Constraint violations are not
    caught here: they are bugs, and the message has to reach the DLQ.
    """

    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def ticket_created(self, event: TicketCreated) -> None:
        self._apply(
            "ticket.created",
            event.escalation_id,
            lambda uow: uow.ticket_events.mark_ticket_open(event.escalation_id, event.ticket_id),
        )

    def ticket_answered(self, event: TicketAnswered) -> None:
        self._apply(
            "ticket.answered",
            event.escalation_id,
            lambda uow: uow.ticket_events.mark_answered(
                event.escalation_id, event.ticket_id, event.answer_text, event.answered_at
            ),
        )

    def ticket_cancelled(self, event: TicketCancelled) -> None:
        """The ticket expired, so the update may be escalated again."""
        self._apply(
            "ticket.cancelled",
            event.escalation_id,
            lambda uow: uow.ticket_events.mark_cancelled(event.escalation_id, event.ticket_id),
        )

    def _apply(
        self, event_type: str, escalation_id: uuid.UUID, change: Callable[[UnitOfWork], bool]
    ) -> None:
        with self._unit_of_work() as uow:
            changed = change(uow)
            uow.commit()
        logger.info(
            f"{event_type} applied" if changed else f"{event_type} duplicate or late, ignored",
            extra={"escalation_id": str(escalation_id)},
        )
