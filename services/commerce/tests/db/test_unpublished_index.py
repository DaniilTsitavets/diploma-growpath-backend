from datetime import UTC, datetime, timedelta

from sqlalchemy import func, text, update
from sqlalchemy.orm import Session

from app.models import CatalogItem, Order
from tests.db.factories import make_order

SCAN = """
SELECT id FROM orders
WHERE placed_event_published_at IS NULL AND placed_at < now() - make_interval(secs => 30)
ORDER BY placed_at LIMIT 10 FOR UPDATE SKIP LOCKED
"""


def test_index_covers_only_unpublished_orders(session: Session):
    definition = session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_orders_unpublished'")
    ).scalar_one()

    assert definition == (
        "CREATE INDEX ix_orders_unpublished ON public.orders USING btree (placed_at)"
        " WHERE (placed_event_published_at IS NULL)"
    )


def test_republisher_scan_uses_the_partial_index(session: Session, item: CatalogItem):
    session.execute(
        text(
            """
            INSERT INTO orders (id, user_id, item_id, idempotency_key, list_price_cents,
                                discount_cents, total_paid_cents, placed_event_published_at)
            SELECT gen_random_uuid(), gen_random_uuid(), :item_id, 'bulk' || g, 1500, 0, 1500, now()
            FROM generate_series(1, 20000) g
            """
        ),
        {"item_id": item.id},
    )
    session.add(make_order(item, placed_at=datetime.now(UTC) - timedelta(minutes=5)))
    session.flush()
    session.execute(text("ANALYZE orders"))

    plan = "\n".join(row[0] for row in session.execute(text(f"EXPLAIN (COSTS OFF) {SCAN}")))

    assert "ix_orders_unpublished" in plan


def test_marking_an_order_published_touches_it_only_once(session: Session, item: CatalogItem):
    order = make_order(item)
    session.add(order)
    session.flush()
    mark = (
        update(Order)
        .where(Order.id == order.id, Order.placed_event_published_at.is_(None))
        .values(placed_event_published_at=func.now())
    )

    first, repeated = session.execute(mark).rowcount, session.execute(mark).rowcount

    assert (first, repeated) == (1, 0)
