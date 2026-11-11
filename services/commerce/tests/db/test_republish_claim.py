import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, insert, text
from sqlalchemy.orm import Session

from app.models import CatalogItem, Order
from app.repository.orders import SqlAlchemyOrderRepository
from tests.db.factories import make_order

GRACE = timedelta(seconds=30)


def minutes_ago(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def test_claims_only_old_unpublished_orders_oldest_first(session: Session, item: CatalogItem):
    newer = make_order(item, idempotency_key="b", placed_at=minutes_ago(5))
    oldest = make_order(item, idempotency_key="a", placed_at=minutes_ago(10))
    fresh = make_order(item, idempotency_key="c")
    published = make_order(
        item,
        idempotency_key="d",
        placed_at=minutes_ago(10),
        placed_event_published_at=minutes_ago(9),
    )
    session.add_all([newer, oldest, fresh, published])
    session.flush()

    claimed = SqlAlchemyOrderRepository(session).claim_unpublished(GRACE, limit=10)

    assert [order.id for order in claimed] == [oldest.id, newer.id]


def test_claim_respects_the_batch_size(session: Session, item: CatalogItem):
    orders = [
        make_order(item, idempotency_key=f"k{i}", placed_at=minutes_ago(10 - i)) for i in range(3)
    ]
    session.add_all(orders)
    session.flush()

    claimed = SqlAlchemyOrderRepository(session).claim_unpublished(GRACE, limit=2)

    assert [order.id for order in claimed] == [orders[0].id, orders[1].id]


def test_a_second_worker_skips_rows_the_first_one_holds(
    engine: Engine, committed_item: CatalogItem
):
    older_id, newer_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Order),
            [
                {
                    "id": order_id,
                    "user_id": uuid.uuid4(),
                    "item_id": committed_item.id,
                    "idempotency_key": "key",
                    "list_price_cents": 2500,
                    "discount_cents": 0,
                    "total_paid_cents": 2500,
                    "placed_at": placed_at,
                }
                for order_id, placed_at in ((older_id, minutes_ago(10)), (newer_id, minutes_ago(9)))
            ],
        )

    with Session(engine) as first, Session(engine) as second:
        first_claim = SqlAlchemyOrderRepository(first).claim_unpublished(GRACE, limit=1)
        # A regression to plain FOR UPDATE would wait on the first worker; fail instead of hanging.
        second.execute(text("SET LOCAL lock_timeout = '2s'"))
        second_claim = SqlAlchemyOrderRepository(second).claim_unpublished(GRACE, limit=10)

    assert [order.id for order in first_claim] == [older_id]
    assert [order.id for order in second_claim] == [newer_id]
