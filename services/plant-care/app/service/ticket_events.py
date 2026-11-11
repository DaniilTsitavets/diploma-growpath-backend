import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TicketCreated:
    escalation_id: uuid.UUID
    ticket_id: uuid.UUID


@dataclass(frozen=True)
class TicketAnswered:
    escalation_id: uuid.UUID
    ticket_id: uuid.UUID
    answer_text: str
    answered_at: datetime


@dataclass(frozen=True)
class TicketCancelled:
    """The ticket expired; there is no cancellation by the user."""

    escalation_id: uuid.UUID
    ticket_id: uuid.UUID
