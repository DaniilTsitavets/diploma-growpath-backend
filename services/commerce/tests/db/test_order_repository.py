import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CatalogItem
from app.repository.orders import SqlAlchemyOrderRepository
from app.service.errors import DiscountAlreadyUsed
from app.service.model import NewOrder


def new_order(item: CatalogItem, user_id: uuid.UUID, key: str = "key-1", discount_cents: int = 0):
    return NewOrder(
        id=uuid.uuid4(),
        user_id=user_id,
        item_id=item.id,
        idempotency_key=key,
        list_price_cents=2500,
        discount_cents=discount_cents,
        total_paid_cents=2500 - discount_cents,
    )


@pytest.fixture
def orders(session: Session) -> SqlAlchemyOrderRepository:
    return SqlAlchemyOrderRepository(session)


def test_place_returns_the_stored_order_with_the_database_timestamp(
    orders: SqlAlchemyOrderRepository, item: CatalogItem
):
    draft = new_order(item, uuid.uuid4(), discount_cents=500)

    placed = orders.place(draft)

    assert (placed.id, placed.discount_cents, placed.total_paid_cents) == (draft.id, 500, 2000)
    assert placed.placed_at is not None
    assert placed.placed_event_published_at is None


def test_find_returns_the_order_of_that_user_and_key(
    orders: SqlAlchemyOrderRepository, item: CatalogItem
):
    user_id = uuid.uuid4()
    placed = orders.place(new_order(item, user_id))
    orders.place(new_order(item, uuid.uuid4()))

    assert orders.find(user_id, "key-1") == placed
    assert orders.find(user_id, "other-key") is None


def test_place_with_a_used_key_returns_none_and_keeps_the_first_order(
    orders: SqlAlchemyOrderRepository, item: CatalogItem
):
    user_id = uuid.uuid4()
    first = orders.place(new_order(item, user_id))

    repeated = orders.place(new_order(item, user_id))

    assert repeated is None
    assert orders.find(user_id, "key-1") == first


def test_second_discount_raises_and_the_transaction_stays_usable(
    orders: SqlAlchemyOrderRepository, item: CatalogItem
):
    user_id = uuid.uuid4()
    orders.place(new_order(item, user_id, "k1", discount_cents=500))

    with pytest.raises(DiscountAlreadyUsed):
        orders.place(new_order(item, user_id, "k2", discount_cents=500))

    retried = orders.place(new_order(item, user_id, "k2"))
    assert retried.total_paid_cents == 2500


def test_has_discounted_order_ignores_zero_discounts_and_other_users(
    orders: SqlAlchemyOrderRepository, item: CatalogItem
):
    user_id = uuid.uuid4()
    orders.place(new_order(item, user_id, "k1"))
    orders.place(new_order(item, uuid.uuid4(), "k1", discount_cents=500))
    assert orders.has_discounted_order(user_id) is False

    orders.place(new_order(item, user_id, "k2", discount_cents=500))

    assert orders.has_discounted_order(user_id) is True


def test_mark_published_succeeds_only_once(orders: SqlAlchemyOrderRepository, item: CatalogItem):
    user_id = uuid.uuid4()
    placed = orders.place(new_order(item, user_id))

    first, repeated = orders.mark_published(placed.id), orders.mark_published(placed.id)

    assert (first, repeated) == (True, False)
    assert orders.find(user_id, "key-1").placed_event_published_at is not None


def test_mark_published_of_an_unknown_order_changes_nothing(orders: SqlAlchemyOrderRepository):
    assert orders.mark_published(uuid.uuid4()) is False


def test_other_integrity_errors_are_not_mistaken_for_a_used_discount(
    orders: SqlAlchemyOrderRepository, item: CatalogItem
):
    broken = new_order(item, uuid.uuid4(), discount_cents=500)
    broken = NewOrder(**{**vars(broken), "total_paid_cents": 1})

    with pytest.raises(IntegrityError):
        orders.place(broken)
