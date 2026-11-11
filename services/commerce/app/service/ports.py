import uuid
from typing import Protocol, Self

from app.service.model import CatalogEntry, Item, NewOrder, OrderData


class OrderRepository(Protocol):
    def find(self, user_id: uuid.UUID, idempotency_key: str) -> OrderData | None: ...

    def has_discounted_order(self, user_id: uuid.UUID) -> bool: ...

    def place(self, order: NewOrder) -> OrderData | None:
        """Store the order. None means the user already has an order with this key.

        Raises DiscountAlreadyUsed when the order carries a discount the user cannot have again.
        """
        ...


class CatalogRepository(Protocol):
    def get_active(self, item_id: uuid.UUID) -> Item | None: ...

    def list_active(self, label: str | None = None) -> list[CatalogEntry]:
        """Active items ordered by name, optionally only those carrying `label`."""
        ...


class UnitOfWork(Protocol):
    """One business action in one transaction: leaving the block without commit rolls back."""

    orders: OrderRepository
    catalog: CatalogRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def commit(self) -> None: ...


class PaymentGateway(Protocol):
    def charge(self, user_id: uuid.UUID, order_id: uuid.UUID, amount_cents: int) -> None:
        """Raises PaymentFailed when the payment is declined."""
        ...
