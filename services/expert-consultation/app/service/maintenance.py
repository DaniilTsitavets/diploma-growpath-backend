import logging
from collections.abc import Callable

from growpath_observability import set_correlation_id

from app.service.events import TicketEvents, terminal_event
from app.service.ports import UnitOfWork
from app.service.ticket_errors import PublishFailed
from app.service.ticket_publisher import TerminalEventPublisher

logger = logging.getLogger(__name__)


class TicketMaintenance:
    """The background work of the service: expire overdue tickets and resend lost events.

    Each step is one transaction per batch. Delivery is at least once: a crash between a publish
    and its record only causes a duplicate, which consumers drop by event id.
    """

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        events: TicketEvents,
        terminal_events: TerminalEventPublisher,
        republish_grace_seconds: float,
        batch_size: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._events = events
        self._terminal_events = terminal_events
        self._grace_seconds = republish_grace_seconds
        self._batch_size = batch_size

    def run_once(self) -> None:
        self.expire_overdue()
        self.republish_terminal()

    def expire_overdue(self) -> int:
        """Cancel the overdue tickets, then announce each; returns how many were cancelled."""
        with self._unit_of_work() as uow:
            expired = uow.tickets.claim_expired(self._batch_size)
            uow.commit()
        for ticket in expired:
            set_correlation_id(str(terminal_event(ticket).event_id))
            logger.info("ticket expired", extra={"ticket_id": str(ticket.id)})
            self._terminal_events.publish_or_leave_to_republisher(ticket)
        return len(expired)

    def republish_terminal(self) -> int:
        """One batch in one transaction; returns how many events were sent again."""
        sent = 0
        with self._unit_of_work() as uow:
            for ticket in uow.tickets.claim_unpublished(self._grace_seconds, self._batch_size):
                event = terminal_event(ticket)
                set_correlation_id(str(event.event_id))
                try:
                    self._events.publish(event)
                except PublishFailed:
                    logger.exception(
                        "republishing a terminal ticket event failed, will retry",
                        extra={"ticket_id": str(ticket.id), "event_type": event.event_type},
                    )
                    continue
                uow.tickets.mark_published(ticket.id)
                logger.info(
                    "terminal ticket event republished",
                    extra={"ticket_id": str(ticket.id), "event_type": event.event_type},
                )
                sent += 1
            uow.commit()
        return sent
