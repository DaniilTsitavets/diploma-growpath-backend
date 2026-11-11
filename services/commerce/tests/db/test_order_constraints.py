import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import CatalogItem, Order
from tests.db.factories import discounted, make_order, make_sensor, violated


def test_order_stores_amounts_and_defaults_its_timestamp(session: Session, item: CatalogItem):
    session.add(make_order(item, **discounted()))
    session.flush()
    session.expire_all()

    order = session.scalars(select(Order)).one()

    assert (order.list_price_cents, order.discount_cents, order.total_paid_cents) == (
        2500,
        500,
        2000,
    )
    assert abs(datetime.now(UTC) - order.placed_at) < timedelta(minutes=1)
    assert order.placed_event_published_at is None


def test_sensor_can_be_ordered(session: Session):
    sensor = make_sensor(price_cents=1500)
    session.add(sensor)
    session.flush()

    session.add(make_order(sensor, list_price_cents=1500, total_paid_cents=1500))
    session.flush()


def test_discount_in_cents_may_equal_the_price_and_the_total_is_zero(
    session: Session, item: CatalogItem
):
    session.add(make_order(item, discount_cents=2500, total_paid_cents=0))

    session.flush()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"discount_cents": 500, "total_paid_cents": 2500}, "ck_orders_amounts"),
        ({"discount_cents": 3000, "total_paid_cents": -500}, "ck_orders_amounts"),
        ({"discount_cents": -1, "total_paid_cents": 2501}, "ck_orders_amounts"),
        ({"idempotency_key": ""}, "ck_orders_idempotency_key_length"),
        ({"idempotency_key": "k" * 129}, "ck_orders_idempotency_key_length"),
    ],
    ids=[
        "total ignores the discount",
        "discount in cents above the price",
        "negative discount in cents",
        "empty idempotency key",
        "idempotency key of 129 characters",
    ],
)
def test_order_rejects_invalid_values(
    session: Session, item: CatalogItem, overrides: dict, constraint: str
):
    with violated(session) as violation:
        session.add(make_order(item, **overrides))

    assert violation.constraint == constraint


def test_idempotency_key_of_128_characters_is_accepted(session: Session, item: CatalogItem):
    session.add(make_order(item, idempotency_key="k" * 128))

    session.flush()


def test_user_cannot_reuse_an_idempotency_key(session: Session, item: CatalogItem):
    user_id = uuid.uuid4()
    session.add(make_order(item, user_id=user_id))
    session.flush()

    with violated(session) as violation:
        session.add(make_order(item, user_id=user_id))

    assert violation.constraint == "uq_orders_user_id_idempotency_key"


def test_two_users_may_use_the_same_idempotency_key(session: Session, item: CatalogItem):
    session.add_all([make_order(item), make_order(item)])

    session.flush()


def test_order_requires_an_existing_item(session: Session, item: CatalogItem):
    with violated(session) as violation:
        session.add(make_order(item, item_id=uuid.uuid4()))

    assert violation.constraint == "fk_orders_item_id_catalog_items"


def test_item_with_orders_cannot_be_deleted(session: Session, item: CatalogItem):
    session.add(make_order(item))
    session.flush()

    with violated(session) as violation:
        session.execute(delete(CatalogItem).where(CatalogItem.id == item.id))

    assert violation.constraint == "fk_orders_item_id_catalog_items"
