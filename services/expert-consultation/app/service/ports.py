import uuid
from datetime import datetime
from typing import Protocol, Self

from app.service.model import NewTicket, QueueCursor, Ticket, TicketSummary


class TicketRepository(Protocol):
    def create(self, ticket: NewTicket, ttl_seconds: int, accept_until: datetime) -> bool:
        """Store the ticket as `open`, expiring `ttl_seconds` after the database clock.

        False when the escalation already has a ticket or the database clock has reached
        `accept_until`; `get_by_escalation` tells the two apart.
        """
        ...

    def get(self, ticket_id: uuid.UUID) -> Ticket | None: ...

    def get_by_escalation(self, escalation_id: uuid.UUID) -> Ticket | None: ...

    def open_queue(self, limit: int, after: QueueCursor | None) -> list[TicketSummary]:
        """Open tickets that have not expired, soonest deadline first; with `after`, only later."""
        ...

    def answer(self, ticket_id: uuid.UUID, expert_id: uuid.UUID, body: str) -> bool:
        """`open` to `answered` with the answer row; False when the ticket is closed or expired."""
        ...

    def claim_expired(self, limit: int) -> list[Ticket]:
        """Cancel open tickets past their deadline, soonest first, skipping rows another worker
        holds, and return them."""
        ...

    def claim_unpublished(self, grace_seconds: float, limit: int) -> list[Ticket]:
        """Lock terminal tickets whose event is not marked published, at least `grace_seconds`
        after the terminal change, skipping locked rows; the locks last until the unit ends."""
        ...

    def mark_published(self, ticket_id: uuid.UUID) -> bool:
        """Record that the terminal event went out; False when it was already recorded."""
        ...


class UnitOfWork(Protocol):
    """One business action in one transaction: leaving the block without commit rolls back."""

    tickets: TicketRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def commit(self) -> None: ...
