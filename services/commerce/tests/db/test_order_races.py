import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import pytest
from sqlalchemy import Connection, Engine, delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.models import CatalogItem, Order
from tests.db.test_discount_uniqueness import order_insert

WAIT_SECONDS = 5


@pytest.fixture
def committed_item(engine: Engine) -> Iterator[CatalogItem]:
    """Races need rows other connections can see, so this item is committed and removed after."""
    item = CatalogItem(id=uuid.uuid4(), name=f"Race {uuid.uuid4()}", price_cents=2500)
    with engine.begin() as connection:
        connection.execute(
            insert(CatalogItem).values(
                id=item.id,
                item_type="pack",
                name=item.name,
                plant_species="Basil",
                seed_count=5,
                price_cents=2500,
            )
        )
    yield item
    with engine.begin() as connection:
        connection.execute(delete(Order).where(Order.item_id == item.id))
        connection.execute(delete(CatalogItem).where(CatalogItem.id == item.id))


def wait_until_blocked(watcher: Connection, pid: int) -> None:
    """Poll until the backend `pid` waits for a lock; no fixed delay is involved."""
    blocked = text(
        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity"
        " WHERE pid = :pid AND wait_event_type = 'Lock')"
    )
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if watcher.execute(blocked, {"pid": pid}).scalar_one():
            return
    raise AssertionError("the second connection never blocked on the first")


def run_contended(
    engine: Engine, first, second, end_first: Literal["commit", "rollback"]
) -> list | IntegrityError:
    """Run `first` and keep it open, start `second` on another connection, wait until it blocks,
    end the first transaction and return the rows of `second` or the error it raised."""
    with (
        engine.connect() as first_connection,
        engine.connect() as second_connection,
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        first_connection.execute(first)
        pid = second_connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        pending = pool.submit(lambda: second_connection.execute(second).all())
        wait_until_blocked(watcher, pid)
        getattr(first_connection, end_first)()
        try:
            outcome = pending.result(timeout=WAIT_SECONDS)
        except IntegrityError as error:
            second_connection.rollback()
            return error
        second_connection.commit()
        return outcome


def count_orders(engine: Engine, user_id: uuid.UUID, key: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            select(func.count()).where(Order.user_id == user_id, Order.idempotency_key == key)
        ).scalar_one()


def test_parallel_discounted_orders_of_one_user_cannot_both_commit(
    engine: Engine, committed_item: CatalogItem
):
    user_id = uuid.uuid4()

    outcome = run_contended(
        engine,
        order_insert(committed_item, user_id, "a", 500),
        order_insert(committed_item, user_id, "b", 500),
        end_first="commit",
    )

    assert isinstance(outcome, IntegrityError)
    assert outcome.orig.diag.constraint_name == "uq_orders_one_discount_per_user"
    assert count_orders(engine, user_id, "b") == 0


@pytest.mark.parametrize(
    ("end_first", "second_inserts"),
    [("commit", False), ("rollback", True)],
    ids=["first commits so the second sees the order", "first rolls back so the second inserts"],
)
def test_parallel_requests_with_one_key_leave_one_order(
    engine: Engine, committed_item: CatalogItem, end_first: str, second_inserts: bool
):
    user_id = uuid.uuid4()

    outcome = run_contended(
        engine,
        order_insert(committed_item, user_id, "same"),
        order_insert(committed_item, user_id, "same"),
        end_first=end_first,
    )

    assert (len(outcome) == 1) is second_inserts
    assert count_orders(engine, user_id, "same") == 1
