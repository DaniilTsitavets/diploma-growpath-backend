import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OrderPlaced:
    order_id: uuid.UUID
    user_id: uuid.UUID
    total_paid_cents: int
    placed_at: datetime


@dataclass(frozen=True)
class TicketCreated:
    escalation_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class TicketCancelled:
    escalation_id: uuid.UUID
    user_id: uuid.UUID
