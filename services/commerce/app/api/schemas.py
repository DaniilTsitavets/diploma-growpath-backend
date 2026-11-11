import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_type: str
    name: str
    plant_species: str | None
    seed_count: int | None
    price_cents: int
    first_purchase_discount_pct: int | None
    labels: list[str]


class CreateOrderRequest(BaseModel):
    item_id: uuid.UUID


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    list_price_cents: int
    discount_cents: int
    total_paid_cents: int
    placed_at: datetime
