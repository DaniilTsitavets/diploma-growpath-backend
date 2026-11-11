import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Self

from growpath_observability import get_correlation_id

from app.config import Settings
from app.service.errors import DiscountAlreadyUsed, PaymentFailed, PublishFailed
from app.service.events import OrderPlaced
from app.service.model import CatalogEntry, Item, NewOrder, OrderData

UNREACHABLE_DATABASE = "postgresql+psycopg://nobody@127.0.0.1:1/none"
TOPIC_ARN = "arn:aws:sns:us-east-1:000000000000:order-placed"


def make_settings(**overrides) -> Settings:
    fields = {"database_url": UNREACHABLE_DATABASE, "order_placed_topic_arn": TOPIC_ARN}
    return Settings(**{**fields, **overrides})


def make_item(**overrides) -> Item:
    fields = {
        "id": uuid.uuid4(),
        "item_type": "pack",
        "name": "Beginner's Kit",
        "plant_species": "Basil",
        "seed_count": 5,
        "price_cents": 2500,
        "first_purchase_discount_pct": 20,
        "is_active": True,
    }
    return Item(**{**fields, **overrides})


def make_entry(labels: tuple[str, ...] = (), **overrides) -> CatalogEntry:
    return CatalogEntry(**vars(make_item(**overrides)), labels=labels)


def make_order_data(**overrides) -> OrderData:
    fields = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "item_id": uuid.uuid4(),
        "idempotency_key": "key-1",
        "list_price_cents": 2500,
        "discount_cents": 0,
        "total_paid_cents": 2500,
        "placed_at": datetime.now(UTC),
        "placed_event_published_at": None,
    }
    return OrderData(**{**fields, **overrides})


class FakeOrders:
    def __init__(self) -> None:
        self.stored: list[OrderData] = []
        self.rejects_discount = False
        self.concurrent_winner: OrderData | None = None
        self.mark_fails = False
        self.last_claim: tuple[timedelta, int] | None = None

    def claim_unpublished(self, grace: timedelta, limit: int) -> list[OrderData]:
        self.last_claim = (grace, limit)
        return [o for o in self.stored if o.placed_event_published_at is None][:limit]

    def mark_published(self, order_id: uuid.UUID) -> bool:
        if self.mark_fails:
            raise RuntimeError("database is gone")
        for position, order in enumerate(self.stored):
            if order.id == order_id and order.placed_event_published_at is None:
                self.stored[position] = replace(order, placed_event_published_at=datetime.now(UTC))
                return True
        return False

    def find(self, user_id: uuid.UUID, idempotency_key: str) -> OrderData | None:
        for order in self.stored:
            if (order.user_id, order.idempotency_key) == (user_id, idempotency_key):
                return order
        return None

    def has_discounted_order(self, user_id: uuid.UUID) -> bool:
        return any(o.user_id == user_id and o.discount_cents > 0 for o in self.stored)

    def place(self, order: NewOrder) -> OrderData | None:
        if self.concurrent_winner is not None:
            self.stored.append(self.concurrent_winner)
            return None
        if order.discount_cents > 0 and self.rejects_discount:
            raise DiscountAlreadyUsed
        placed = make_order_data(**vars(order))
        self.stored.append(placed)
        return placed


class FakeCatalog:
    def __init__(self, *items: Item) -> None:
        self._items = {item.id: item for item in items}

    def deactivate(self, item_id: uuid.UUID) -> None:
        self._items[item_id] = replace(self._items[item_id], is_active=False)

    def get(self, item_id: uuid.UUID) -> Item | None:
        return self._items.get(item_id)

    def get_active(self, item_id: uuid.UUID) -> Item | None:
        item = self.get(item_id)
        return item if item is not None and item.is_active else None

    def list_active(self, label: str | None = None) -> list[CatalogEntry]:
        entries = [
            item if isinstance(item, CatalogEntry) else CatalogEntry(**vars(item), labels=())
            for item in self._items.values()
            if item.is_active
        ]
        return sorted(
            (entry for entry in entries if label is None or label in entry.labels),
            key=lambda entry: entry.name,
        )


class FakeUnitOfWork:
    def __init__(self, orders: FakeOrders, catalog: FakeCatalog) -> None:
        self.orders = orders
        self.catalog = catalog
        self.committed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def commit(self) -> None:
        self.committed = True


class RecordingEvents:
    def __init__(self, fails: bool = False) -> None:
        self.published: list[OrderPlaced] = []
        self.correlation_ids: list[str | None] = []
        self.fails = fails
        self.failing_orders: set[uuid.UUID] = set()

    def publish_order_placed(self, event: OrderPlaced) -> None:
        if self.fails or event.order_id in self.failing_orders:
            raise PublishFailed
        self.published.append(event)
        self.correlation_ids.append(get_correlation_id())


class RecordingPayments:
    def __init__(self, declines: bool = False) -> None:
        self.charges: list[int] = []
        self._declines = declines

    def charge(self, user_id: uuid.UUID, order_id: uuid.UUID, amount_cents: int) -> None:
        if self._declines:
            raise PaymentFailed
        self.charges.append(amount_cents)
