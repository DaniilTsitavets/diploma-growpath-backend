from contextvars import copy_context
from datetime import timedelta

from app.service.events import order_placed
from app.service.republisher import OrderRepublisher
from tests.unit.fakes import (
    FakeCatalog,
    FakeOrders,
    FakeUnitOfWork,
    RecordingEvents,
    make_item,
    make_order_data,
)

GRACE = timedelta(seconds=30)


class Scenario:
    def __init__(self, *items) -> None:
        self.orders = FakeOrders()
        self.events = RecordingEvents()
        self.uow = FakeUnitOfWork(self.orders, FakeCatalog(*items))
        self.republisher = OrderRepublisher(lambda: self.uow, self.events, GRACE, batch_size=10)

    def pending_order(self, item, **overrides):
        order = make_order_data(item_id=item.id, **overrides)
        self.orders.stored.append(order)
        return order

    def run(self) -> int:
        return copy_context().run(self.republisher.run_once)


def test_pending_orders_are_published_identically_and_marked():
    item = make_item()
    scenario = Scenario(item)
    first, second = scenario.pending_order(item), scenario.pending_order(item)

    published = scenario.run()

    assert published == 2
    assert scenario.events.published == [order_placed(first, item), order_placed(second, item)]
    assert all(o.placed_event_published_at is not None for o in scenario.orders.stored)
    assert scenario.uow.committed is True


def test_nothing_pending_publishes_nothing():
    scenario = Scenario(make_item())

    assert scenario.run() == 0
    assert scenario.events.published == []


def test_a_failing_order_stays_pending_and_does_not_block_the_others():
    item = make_item()
    scenario = Scenario(item)
    failing, healthy = scenario.pending_order(item), scenario.pending_order(item)
    scenario.events.failing_orders.add(failing.id)

    published = scenario.run()

    assert published == 1
    assert [e.order_id for e in scenario.events.published] == [healthy.id]
    stored = {o.id: o.placed_event_published_at for o in scenario.orders.stored}
    assert stored[failing.id] is None
    assert stored[healthy.id] is not None


def test_an_order_with_a_missing_item_is_skipped():
    item = make_item()
    scenario = Scenario(item)
    scenario.pending_order(make_item())
    healthy = scenario.pending_order(item)

    scenario.run()

    assert [e.order_id for e in scenario.events.published] == [healthy.id]


def test_each_event_is_published_under_its_order_id_as_correlation_id():
    item = make_item()
    scenario = Scenario(item)
    order = scenario.pending_order(item)

    scenario.run()

    assert scenario.events.correlation_ids == [str(order.id)]


def test_grace_period_and_batch_size_are_passed_to_the_repository():
    scenario = Scenario(make_item())

    scenario.run()

    assert scenario.orders.last_claim == (GRACE, 10)
