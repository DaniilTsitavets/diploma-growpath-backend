import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

ANSWER_LIMIT = 5000
# Spaces, tabs and line breaks do not count as text; the same set as `ck_answers_body`.
BLANK_CHARACTERS = " \t\r\n"


class TicketStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NewTicket:
    id: uuid.UUID
    escalation_id: uuid.UUID
    user_id: uuid.UUID
    journal_id: uuid.UUID
    update_id: uuid.UUID
    context: dict[str, Any]


@dataclass(frozen=True)
class Answer:
    expert_id: uuid.UUID
    body: str


@dataclass(frozen=True)
class Ticket:
    id: uuid.UUID
    escalation_id: uuid.UUID
    user_id: uuid.UUID
    journal_id: uuid.UUID
    update_id: uuid.UUID
    context: dict[str, Any]
    status: TicketStatus
    created_at: datetime
    expires_at: datetime
    answered_at: datetime | None
    cancelled_at: datetime | None
    terminal_event_published_at: datetime | None
    answer: Answer | None


@dataclass(frozen=True)
class TicketSummary:
    """What the expert queue shows before a ticket is opened."""

    id: uuid.UUID
    species: str | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class QueueCursor:
    expires_at: datetime
    id: uuid.UUID
