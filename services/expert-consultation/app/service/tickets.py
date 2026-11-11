import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.service.events import EscalationRequested, TicketEvents, created_event
from app.service.model import NewTicket, Ticket, TicketStatus
from app.service.ports import UnitOfWork
from app.service.ticket_errors import TicketAlreadyAnswered, TicketExpired, TicketNotFound
from app.service.ticket_publisher import TerminalEventPublisher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnswerResult:
    ticket: Ticket
    created: bool


class TicketService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        events: TicketEvents,
        terminal_events: TerminalEventPublisher,
        ttl_seconds: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._events = events
        self._terminal_events = terminal_events
        self._ttl_seconds = ttl_seconds

    def create_from_escalation(self, request: EscalationRequested) -> None:
        """Open the ticket and announce it, so the caller may acknowledge the message.

        A PublishFailed or a database error leaves the message in the queue; its redelivery finds
        the ticket and takes the republish path.
        """
        ticket, created = self._open_ticket(request)
        if ticket is None:
            logger.info(
                "escalation arrived after its acceptance window, no ticket created",
                extra={
                    "escalation_id": str(request.escalation_id),
                    "accept_until": request.accept_until.isoformat(),
                },
            )
            return
        self._events.publish(created_event(ticket))
        if ticket.status is not TicketStatus.OPEN:
            self._terminal_events.publish(ticket)
        logger.info(
            "ticket created" if created else "ticket events republished for a redelivery",
            extra={"ticket_id": str(ticket.id), "escalation_id": str(ticket.escalation_id)},
        )

    def answer(self, ticket_id: uuid.UUID, expert_id: uuid.UUID, body: str) -> AnswerResult:
        """Record the answer; repeating it as the same expert returns the stored one."""
        with self._unit_of_work() as uow:
            if uow.tickets.answer(ticket_id, expert_id, body):
                ticket = uow.tickets.get(ticket_id)
                uow.commit()
                created = True
            else:
                ticket = uow.tickets.get(ticket_id)
                created = False
        if ticket is None:
            raise TicketNotFound
        if not created:
            self._require_answered_by(ticket, expert_id)
        self._terminal_events.publish_or_leave_to_republisher(ticket)
        logger.info(
            "ticket answered" if created else "answer repeated, event sent again",
            extra={"ticket_id": str(ticket.id)},
        )
        return AnswerResult(ticket, created)

    def _open_ticket(self, request: EscalationRequested) -> tuple[Ticket | None, bool]:
        ticket_id = uuid.uuid4()
        new_ticket = NewTicket(
            ticket_id,
            request.escalation_id,
            request.user_id,
            request.journal_id,
            request.update_id,
            request.context,
        )
        with self._unit_of_work() as uow:
            if uow.tickets.create(new_ticket, self._ttl_seconds, request.accept_until):
                ticket = uow.tickets.get(ticket_id)
                uow.commit()
                return ticket, True
            return uow.tickets.get_by_escalation(request.escalation_id), False

    @staticmethod
    def _require_answered_by(ticket: Ticket, expert_id: uuid.UUID) -> None:
        """The refused answer is accepted only as the same expert's own retry."""
        if ticket.status is TicketStatus.ANSWERED:
            if ticket.answer.expert_id == expert_id:
                return
            raise TicketAlreadyAnswered
        # A ticket that is still `open` here missed the guard on its deadline; the sweep has not
        # cancelled it yet.
        raise TicketExpired
