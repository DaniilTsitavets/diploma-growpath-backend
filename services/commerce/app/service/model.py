import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Item:
    id: uuid.UUID
    item_type: str
    name: str
    plant_species: str | None
    seed_count: int | None
    price_cents: int
    first_purchase_discount_pct: int | None
    is_active: bool


@dataclass(frozen=True)
class CatalogEntry(Item):
    labels: tuple[str, ...]


@dataclass(frozen=True)
class NewOrder:
    id: uuid.UUID
    user_id: uuid.UUID
    item_id: uuid.UUID
    idempotency_key: str
    list_price_cents: int
    discount_cents: int
    total_paid_cents: int


@dataclass(frozen=True)
class OrderData:
    id: uuid.UUID
    user_id: uuid.UUID
    item_id: uuid.UUID
    idempotency_key: str
    list_price_cents: int
    discount_cents: int
    total_paid_cents: int
    placed_at: datetime
    placed_event_published_at: datetime | None


@dataclass(frozen=True)
class PlaceOrder:
    user_id: uuid.UUID
    item_id: uuid.UUID
    idempotency_key: str


@dataclass(frozen=True)
class PlacedOrder:
    order: OrderData
    created: bool
