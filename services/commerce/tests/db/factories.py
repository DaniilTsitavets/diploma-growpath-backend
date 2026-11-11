import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CatalogItem, Order


@dataclass
class Violation:
    constraint: str | None = None


@contextmanager
def violated(session: Session) -> Iterator[Violation]:
    """Run the block in a savepoint and record which constraint the database reported."""
    violation = Violation()
    try:
        with session.begin_nested():
            yield violation
    except IntegrityError as error:
        violation.constraint = error.orig.diag.constraint_name
    else:
        raise AssertionError("the database accepted a row that should be rejected")


def make_item(**overrides) -> CatalogItem:
    fields = {
        "item_type": "pack",
        "name": "Basil Kit",
        "plant_species": "Basil",
        "seed_count": 5,
        "price_cents": 2500,
    }
    return CatalogItem(**{**fields, **overrides})


def make_sensor(**overrides) -> CatalogItem:
    fields = {
        "item_type": "sensor",
        "name": "Soil Sensor",
        "plant_species": None,
        "seed_count": None,
    }
    return make_item(**{**fields, **overrides})


def make_order(item: CatalogItem, **overrides) -> Order:
    fields = {
        "user_id": uuid.uuid4(),
        "item_id": item.id,
        "idempotency_key": "key-1",
        "list_price_cents": 2500,
        "discount_cents": 0,
        "total_paid_cents": 2500,
    }
    return Order(**{**fields, **overrides})


def discounted(**overrides) -> dict:
    """Amounts of an order with the 20% first-purchase discount on a 2500-cent item."""
    return {"discount_cents": 500, "total_paid_cents": 2000, **overrides}
