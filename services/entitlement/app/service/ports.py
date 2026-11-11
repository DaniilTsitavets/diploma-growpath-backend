import uuid
from datetime import datetime, timedelta
from typing import Protocol, Self

from app.service.model import NewPlacedOrder, QueueMessage, Reservation, ReservationStatus


class PlacedOrderRepository(Protocol):
    def add(self, order: NewPlacedOrder) -> bool:
        """Store the order; False when it was already stored (the event id is the order id)."""
        ...

    def window_total(self, user_id: uuid.UUID) -> int:
        """Cents paid by the user in the last 30 days."""
        ...

    def tier_expires_at(self, user_id: uuid.UUID, total: int, threshold: int) -> datetime | None:
        """When the window sum falls below `threshold` as the oldest orders leave the window."""
        ...


class ReservationRepository(Protocol):
    def lock_user(self, user_id: uuid.UUID) -> None:
        """Serialize the user's reservations until the transaction ends.

        Raises LockTimeout when the lock is not granted in time.
        """
        ...

    def get(self, reservation_id: uuid.UUID) -> Reservation | None: ...

    def get_owned(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> Reservation | None: ...

    def count_used(self, user_id: uuid.UUID) -> int:
        """Reservations of the user that count against the quota of the current period."""
        ...

    def insert(
        self, reservation_id: uuid.UUID, user_id: uuid.UUID, ttl: timedelta
    ) -> Reservation | None:
        """None when a reservation with this id already exists."""
        ...

    def transition(
        self,
        reservation_id: uuid.UUID,
        user_id: uuid.UUID,
        target: ReservationStatus,
        sources: frozenset[ReservationStatus],
        unexpired_only: bool,
    ) -> Reservation | None:
        """Move the user's reservation from one of `sources` to `target`; None when none matched."""
        ...


class MessageQueue(Protocol):
    def receive(self, max_messages: int, wait_seconds: int) -> list[QueueMessage]:
        """Long-poll for messages. Raises QueueUnavailable when the queue does not answer."""
        ...

    def delete(self, message: QueueMessage) -> None:
        """Acknowledge a message. Raises QueueUnavailable when the queue does not answer."""
        ...

    def check(self) -> None:
        """Raises QueueUnavailable when the queue cannot be reached."""
        ...


class UnitOfWork(Protocol):
    """One business action in one transaction: leaving the block without commit rolls back."""

    placed_orders: PlacedOrderRepository
    ai_reservations: ReservationRepository
    expert_reservations: ReservationRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def commit(self) -> None: ...
