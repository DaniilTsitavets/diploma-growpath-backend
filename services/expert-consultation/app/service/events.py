import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Protocol

from app.service.model import Ticket, TicketStatus

NS_GROWPATH_EVENTS = uuid.UUID("6f1b2c4e-8a3d-4e5f-9b7a-1c2d3e4f5a6b")
CREATED = "ticket.created"
ANSWERED = "ticket.answered"
CANCELLED = "ticket.cancelled"


@dataclass(frozen=True)
class EscalationRequested:
    """The `escalation.requested` event this service consumes."""

    event_id: uuid.UUID
    occurred_at: datetime
    escalation_id: uuid.UUID
    user_id: uuid.UUID
    journal_id: uuid.UUID
    update_id: uuid.UUID
    accept_until: datetime
    context: dict[str, Any]


@dataclass(frozen=True)
class TicketCreated:
    event_type: ClassVar[str] = CREATED

    event_id: uuid.UUID
    occurred_at: datetime
    ticket_id: uuid.UUID
    escalation_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class TicketAnswered:
    event_type: ClassVar[str] = ANSWERED

    event_id: uuid.UUID
    occurred_at: datetime
    ticket_id: uuid.UUID
    escalation_id: uuid.UUID
    user_id: uuid.UUID
    journal_id: uuid.UUID
    answer_text: str
    answered_at: datetime


@dataclass(frozen=True)
class TicketCancelled:
    """The ticket expired; there is no cancellation by the user."""

    event_type: ClassVar[str] = CANCELLED

    event_id: uuid.UUID
    occurred_at: datetime
    ticket_id: uuid.UUID
    escalation_id: uuid.UUID
    user_id: uuid.UUID


TicketEvent = TicketCreated | TicketAnswered | TicketCancelled


class TicketEvents(Protocol):
    def publish(self, event: TicketEvent) -> None:
        """Raises PublishFailed when the broker does not accept the event."""
        ...


def event_id(ticket_id: uuid.UUID, event_type: str) -> uuid.UUID:
    """Stable per ticket and type, so consumers drop a republished event as a duplicate."""
    return uuid.uuid5(NS_GROWPATH_EVENTS, f"{ticket_id}:{event_type}")


def created_event(ticket: Ticket) -> TicketCreated:
    return TicketCreated(
        event_id=event_id(ticket.id, CREATED),
        occurred_at=ticket.created_at,
        ticket_id=ticket.id,
        escalation_id=ticket.escalation_id,
        user_id=ticket.user_id,
    )


def terminal_event(ticket: Ticket) -> TicketAnswered | TicketCancelled:
    """Built only from the stored rows, so a republished event equals the first one."""
    if ticket.status is TicketStatus.ANSWERED and ticket.answer and ticket.answered_at:
        return TicketAnswered(
            event_id=event_id(ticket.id, ANSWERED),
            occurred_at=ticket.answered_at,
            ticket_id=ticket.id,
            escalation_id=ticket.escalation_id,
            user_id=ticket.user_id,
            journal_id=ticket.journal_id,
            answer_text=ticket.answer.body,
            answered_at=ticket.answered_at,
        )
    if ticket.status is TicketStatus.CANCELLED and ticket.cancelled_at:
        return TicketCancelled(
            event_id=event_id(ticket.id, CANCELLED),
            occurred_at=ticket.cancelled_at,
            ticket_id=ticket.id,
            escalation_id=ticket.escalation_id,
            user_id=ticket.user_id,
        )
    raise ValueError(f"ticket {ticket.id} has no terminal event in status {ticket.status}")
