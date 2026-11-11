import uuid
from dataclasses import dataclass
from datetime import datetime

from app.service.model import Item, OrderData


@dataclass(frozen=True)
class OrderPlaced:
    """The `order.placed` event; its id is the order id, so consumers deduplicate on it."""

    event_id: uuid.UUID
    occurred_at: datetime
    order_id: uuid.UUID
    user_id: uuid.UUID
    item_id: uuid.UUID
    item_type: str
    plant_species: str | None
    seed_count: int | None
    total_paid_cents: int
    discount_applied: bool
    placed_at: datetime


def order_placed(order: OrderData, item: Item) -> OrderPlaced:
    """Built only from the stored rows, so a republished event equals the first one."""
    return OrderPlaced(
        event_id=order.id,
        occurred_at=order.placed_at,
        order_id=order.id,
        user_id=order.user_id,
        item_id=order.item_id,
        item_type=item.item_type,
        plant_species=item.plant_species,
        seed_count=item.seed_count,
        total_paid_cents=order.total_paid_cents,
        discount_applied=order.discount_cents > 0,
        placed_at=order.placed_at,
    )
