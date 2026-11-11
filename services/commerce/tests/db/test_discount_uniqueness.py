import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import CatalogItem, Order
from tests.db.factories import violated


def order_insert(item: CatalogItem, user_id: uuid.UUID, key: str, discount_cents: int = 0):
    """The documented purchase INSERT: a repeated key is a no-op, not an error."""
    return (
        insert(Order)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            item_id=item.id,
            idempotency_key=key,
            list_price_cents=2500,
            discount_cents=discount_cents,
            total_paid_cents=2500 - discount_cents,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
        .returning(Order.id)
    )


def test_second_discount_for_a_user_names_the_discount_index(session: Session, item: CatalogItem):
    user_id = uuid.uuid4()
    session.execute(order_insert(item, user_id, "k1", 500))

    with violated(session) as violation:
        session.execute(order_insert(item, user_id, "k2", 500))

    assert violation.constraint == "uq_orders_one_discount_per_user"


def test_repeated_key_inserts_nothing_even_with_a_discount(session: Session, item: CatalogItem):
    user_id = uuid.uuid4()
    session.execute(order_insert(item, user_id, "k1", 500))

    repeated = session.execute(order_insert(item, user_id, "k1", 500))

    assert repeated.first() is None


def test_retry_without_discount_after_the_discount_was_taken(session: Session, item: CatalogItem):
    user_id = uuid.uuid4()
    session.execute(order_insert(item, user_id, "k1", 500))
    with violated(session):
        session.execute(order_insert(item, user_id, "k2", 500))

    retried = session.execute(order_insert(item, user_id, "k2", 0))

    assert retried.first() is not None


def test_order_with_zero_cent_discount_does_not_use_up_the_right(
    session: Session, item: CatalogItem
):
    user_id = uuid.uuid4()
    session.execute(order_insert(item, user_id, "k1", 0))

    discounted = session.execute(order_insert(item, user_id, "k2", 500))

    assert discounted.first() is not None


def test_each_user_has_their_own_discount(session: Session, item: CatalogItem):
    session.execute(order_insert(item, uuid.uuid4(), "k1", 500))

    other_user = session.execute(order_insert(item, uuid.uuid4(), "k1", 500))

    assert other_user.first() is not None
