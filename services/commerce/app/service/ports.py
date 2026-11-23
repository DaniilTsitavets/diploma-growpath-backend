import uuid
from datetime import timedelta
from typing import Protocol, Self

from app.service.events import OrderPlaced
from app.service.model import CatalogEntry, Item, NewOrder, OrderData


class OrderRepository(Protocol):
    def find(self, user_id: uuid.UUID, idempotency_key: str) -> OrderData | None: ...

    def has_discounted_order(self, user_id: uuid.UUID) -> bool: ...

    def claim_unpublished(self, grace: timedelta, limit: int) -> list[OrderData]:
        """Oldest orders without a published event that are older than `grace`.

        The rows stay locked until the transaction ends, and rows another worker holds are skipped.
        """
        ...

    def mark_published(self, order_id: uuid.UUID) -> bool:
        """Record that `order.placed` went out; True only the first time."""
        ...

    def place(self, order: NewOrder) -> OrderData | None:
        """Store the order. None means the user already has an order with this key.

        Raises DiscountAlreadyUsed when the order carries a discount the user cannot have again.
        """
        ...


class CatalogRepository(Protocol):
    def get(self, item_id: uuid.UUID) -> Item | None:
        """The item whether or not it is still active."""
        ...

    def get_active(self, item_id: uuid.UUID) -> Item | None: ...

    def find_active_by_slug(self, slug: str) -> CatalogEntry | None: ...

    def list_active(
        self, label: str | None = None, *, limit: int | None = None, after_slug: str | None = None
    ) -> list[CatalogEntry]:
        """Active items ordered by slug, optionally only those carrying `label`.

        `after_slug` keeps only slugs that sort after it; `limit` caps the number of items.

        Raises CatalogUnavailable (also from find_active_by_slug) when the database is unreachable.
        """
        ...


class UnitOfWork(Protocol):
    """One business action in one transaction: leaving the block without commit rolls back."""

    orders: OrderRepository
    catalog: CatalogRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def commit(self) -> None: ...


class OrderEvents(Protocol):
    def publish_order_placed(self, event: OrderPlaced) -> None:
        """Raises PublishFailed when the broker does not accept the event."""
        ...


class PaymentGateway(Protocol):
    def charge(self, user_id: uuid.UUID, order_id: uuid.UUID, amount_cents: int) -> None:
        """Raises PaymentFailed when the payment is declined."""
        ...
