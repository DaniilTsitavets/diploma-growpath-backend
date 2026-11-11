import logging
from collections.abc import Callable

from app.service.events import TicketEvents, terminal_event
from app.service.model import Ticket
from app.service.ports import UnitOfWork
from app.service.ticket_errors import PublishFailed

logger = logging.getLogger(__name__)


class TerminalEventPublisher:
    """Sends the terminal event of a committed ticket and records that it went out."""

    def __init__(self, unit_of_work: Callable[[], UnitOfWork], events: TicketEvents) -> None:
        self._unit_of_work = unit_of_work
        self._events = events

    def publish(self, ticket: Ticket) -> None:
        """Raises PublishFailed; nothing is recorded then, so the republisher sends it later."""
        self._events.publish(terminal_event(ticket))
        with self._unit_of_work() as uow:
            uow.tickets.mark_published(ticket.id)
            uow.commit()

    def publish_or_leave_to_republisher(self, ticket: Ticket) -> None:
        try:
            self.publish(ticket)
        except PublishFailed:
            logger.exception(
                "terminal ticket event not published, the republisher will retry",
                extra={"ticket_id": str(ticket.id), "status": ticket.status.value},
            )
