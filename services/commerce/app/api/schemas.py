import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

CURRENCY = "EUR"


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_type: str
    name: str
    slug: str
    plant_slug: str | None
    plant_species: str | None
    seed_count: int | None
    price_cents: int
    currency: Literal["EUR"] = CURRENCY
    first_purchase_discount_pct: int | None
    labels: list[str]


class ItemPage(BaseModel):
    items: list[ItemResponse]
    next_cursor: str | None


class CreateOrderRequest(BaseModel):
    item_id: uuid.UUID


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    list_price_cents: int
    discount_cents: int
    total_paid_cents: int
    currency: Literal["EUR"] = CURRENCY
    placed_at: datetime


class Problem(BaseModel):
    """Problem details (RFC 7807) with a stable kebab-case `code`."""

    type: str
    title: str
    status: int
    detail: str | None
    correlation_id: str | None
    code: str
    errors: list[dict[str, Any]] | None = None
