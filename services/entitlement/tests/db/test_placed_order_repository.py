import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PlacedOrder
from app.repository.placed_orders import SqlAlchemyPlacedOrderRepository
from app.service.model import NewPlacedOrder
from tests.db.factories import add_placed_order, db_now


def test_adding_an_order_twice_keeps_the_first(session: Session):
    repository = SqlAlchemyPlacedOrderRepository(session)
    order = NewPlacedOrder(uuid.uuid4(), uuid.uuid4(), 2000, db_now(session))

    first = repository.add(order)
    duplicate = repository.add(NewPlacedOrder(order.order_id, order.user_id, 9999, order.placed_at))

    assert (first, duplicate) == (True, False)
    assert session.scalars(select(PlacedOrder.total_paid_cents)).all() == [2000]


def test_added_order_keeps_the_placement_time_of_the_event(session: Session):
    repository = SqlAlchemyPlacedOrderRepository(session)
    placed_at = db_now(session) - timedelta(days=3)

    repository.add(NewPlacedOrder(uuid.uuid4(), uuid.uuid4(), 100, placed_at))

    assert session.scalar(select(PlacedOrder.placed_at)) == placed_at


def test_window_total_of_a_user_without_orders_is_zero(session: Session):
    assert SqlAlchemyPlacedOrderRepository(session).window_total(uuid.uuid4()) == 0


def test_window_total_sums_the_last_30_days_of_one_user(session: Session):
    user_id = uuid.uuid4()
    now = db_now(session)
    add_placed_order(session, user_id=user_id, total_paid_cents=1500, placed_at=now)
    add_placed_order(
        session, user_id=user_id, total_paid_cents=2500, placed_at=now - timedelta(days=29)
    )
    add_placed_order(session, total_paid_cents=7000, placed_at=now)

    assert SqlAlchemyPlacedOrderRepository(session).window_total(user_id) == 4000


def test_orders_leave_the_window_after_exactly_30_days(session: Session):
    user_id = uuid.uuid4()
    now = db_now(session)
    add_placed_order(
        session, user_id=user_id, total_paid_cents=100, placed_at=now - timedelta(days=30)
    )
    add_placed_order(
        session, user_id=user_id, total_paid_cents=200, placed_at=now - timedelta(days=31)
    )
    add_placed_order(
        session,
        user_id=user_id,
        total_paid_cents=400,
        placed_at=now - timedelta(days=30) + timedelta(microseconds=1),
    )

    assert SqlAlchemyPlacedOrderRepository(session).window_total(user_id) == 400


def test_tier_ends_when_the_oldest_order_leaves_and_the_sum_drops_below_the_threshold(
    session: Session,
):
    user_id = uuid.uuid4()
    now = db_now(session)
    add_placed_order(
        session, user_id=user_id, total_paid_cents=3000, placed_at=now - timedelta(days=25)
    )
    add_placed_order(
        session, user_id=user_id, total_paid_cents=3000, placed_at=now - timedelta(days=10)
    )

    expires_at = SqlAlchemyPlacedOrderRepository(session).tier_expires_at(
        user_id, total=6000, threshold=4000
    )

    assert expires_at == now + timedelta(days=5)


def test_tier_survives_the_departure_of_an_order_that_leaves_the_sum_at_the_threshold(
    session: Session,
):
    user_id = uuid.uuid4()
    now = db_now(session)
    add_placed_order(
        session, user_id=user_id, total_paid_cents=1000, placed_at=now - timedelta(days=25)
    )
    add_placed_order(
        session, user_id=user_id, total_paid_cents=4000, placed_at=now - timedelta(days=10)
    )

    expires_at = SqlAlchemyPlacedOrderRepository(session).tier_expires_at(
        user_id, total=5000, threshold=4000
    )

    assert expires_at == now + timedelta(days=20)


def test_tier_end_ignores_orders_of_other_users_and_old_orders(session: Session):
    user_id = uuid.uuid4()
    now = db_now(session)
    add_placed_order(
        session, user_id=user_id, total_paid_cents=5000, placed_at=now - timedelta(days=2)
    )
    add_placed_order(session, total_paid_cents=9000, placed_at=now - timedelta(days=28))
    add_placed_order(
        session, user_id=user_id, total_paid_cents=9000, placed_at=now - timedelta(days=40)
    )

    expires_at = SqlAlchemyPlacedOrderRepository(session).tier_expires_at(
        user_id, total=5000, threshold=4000
    )

    assert expires_at == now + timedelta(days=28)


def test_tier_end_of_a_user_without_orders_is_unknown(session: Session):
    repository = SqlAlchemyPlacedOrderRepository(session)

    assert repository.tier_expires_at(uuid.uuid4(), total=0, threshold=4000) is None


def test_tier_query_reads_the_covering_index(session: Session):
    user_id = uuid.uuid4()
    add_placed_order(session, user_id=user_id)
    session.connection().exec_driver_sql("SET LOCAL enable_seqscan = off")

    plan = "\n".join(
        session.connection()
        .exec_driver_sql(
            "EXPLAIN SELECT COALESCE(SUM(total_paid_cents), 0) FROM placed_orders"
            " WHERE user_id = %s AND placed_at > now() - interval '30 days'",
            (user_id,),
        )
        .scalars()
    )

    assert "ix_placed_orders_user_placed" in plan
