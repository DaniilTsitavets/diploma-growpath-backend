import logging
import uuid

import pytest
from growpath_observability import get_correlation_id, set_correlation_id

from app.messaging.codec import TICKET_EVENTS_IGNORED, order_routes, ticket_routes
from app.service.consumer import QueueConsumer
from app.service.errors import QueueUnavailable
from app.service.event_handlers import EventHandlers
from tests.unit.fakes import (
    COMMITTED,
    RELEASED,
    RESERVED,
    FakeQueue,
    FakeUnitOfWork,
    make_expert_kind,
    make_quota,
    order_placed_body,
    ticket_body,
)

USER = uuid.uuid4()


class Harness:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.uow = FakeUnitOfWork(self.log)
        self.orders = FakeQueue(self.log)
        self.tickets = FakeQueue(self.log)
        handlers = EventHandlers(lambda: self.uow, make_quota(make_expert_kind(), self.uow))
        self.order_consumer = QueueConsumer(
            self.orders, order_routes(handlers), frozenset(), batch_size=10, wait_seconds=1
        )
        self.ticket_consumer = QueueConsumer(
            self.tickets, ticket_routes(handlers), TICKET_EVENTS_IGNORED, 10, 1
        )


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_order_placed_is_stored_and_deleted_only_after_the_commit(harness: Harness):
    body = order_placed_body(user_id=str(USER), total_paid_cents=2500)
    harness.orders.send("order.placed", body)

    received = harness.order_consumer.run_once()

    (stored,) = harness.uow.placed_orders.orders.values()
    assert received == 1
    assert (stored.user_id, stored.total_paid_cents) == (USER, 2500)
    assert str(stored.order_id) == body["order_id"]
    assert harness.log == ["commit", "delete"]


def test_a_redelivered_order_is_counted_once_and_still_acknowledged(harness: Harness):
    body = order_placed_body(user_id=str(USER), total_paid_cents=2500)
    harness.orders.send("order.placed", body)
    harness.orders.send("order.placed", body)

    harness.order_consumer.run_once()

    assert harness.uow.placed_orders.window_total(USER) == 2500
    assert len(harness.orders.deleted) == 2


def test_ticket_created_commits_the_reservation(harness: Harness):
    reservation = harness.uow.expert_reservations.seed(USER)
    harness.tickets.send("ticket.created", ticket_body(reservation.id, USER))

    harness.ticket_consumer.run_once()

    assert harness.uow.expert_reservations.rows[reservation.id].status is COMMITTED
    assert harness.log == ["commit", "delete"]


def test_ticket_created_commits_even_after_the_reservation_expired(harness: Harness):
    reservation = harness.uow.expert_reservations.seed(USER, unexpired=False)
    harness.tickets.send("ticket.created", ticket_body(reservation.id, USER))

    harness.ticket_consumer.run_once()

    assert harness.uow.expert_reservations.rows[reservation.id].status is COMMITTED


def test_a_duplicate_ticket_created_is_acknowledged(harness: Harness):
    reservation = harness.uow.expert_reservations.seed(USER)
    body = ticket_body(reservation.id, USER)
    harness.tickets.send("ticket.created", body)
    harness.tickets.send("ticket.created", body)

    harness.ticket_consumer.run_once()

    assert harness.uow.expert_reservations.rows[reservation.id].status is COMMITTED
    assert len(harness.tickets.deleted) == 2


@pytest.mark.parametrize("status", [RESERVED, COMMITTED])
def test_ticket_cancelled_releases_a_reserved_or_committed_reservation(harness: Harness, status):
    reservation = harness.uow.expert_reservations.seed(USER, status=status)
    harness.tickets.send("ticket.cancelled", ticket_body(reservation.id, USER))

    harness.ticket_consumer.run_once()

    assert harness.uow.expert_reservations.rows[reservation.id].status is RELEASED
    assert len(harness.tickets.deleted) == 1


def test_ticket_cancelled_before_ticket_created_leaves_the_reservation_released(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    reservation = harness.uow.expert_reservations.seed(USER)
    harness.tickets.send("ticket.cancelled", ticket_body(reservation.id, USER))
    harness.tickets.send("ticket.created", ticket_body(reservation.id, USER))

    with caplog.at_level(logging.WARNING, logger="app.service.event_handlers"):
        harness.ticket_consumer.run_once()

    assert harness.uow.expert_reservations.rows[reservation.id].status is RELEASED
    assert len(harness.tickets.deleted) == 2
    assert [r.message for r in caplog.records] == [
        "event ignored, the reservation cannot make this transition"
    ]


@pytest.mark.parametrize("event_type", ["ticket.created", "ticket.cancelled"])
def test_a_ticket_event_for_an_unknown_reservation_is_acknowledged(
    harness: Harness, event_type: str
):
    harness.tickets.send(event_type, ticket_body(uuid.uuid4(), USER))

    harness.ticket_consumer.run_once()

    assert len(harness.tickets.deleted) == 1


def test_ticket_event_of_another_user_does_not_touch_the_reservation(harness: Harness):
    reservation = harness.uow.expert_reservations.seed(uuid.uuid4())
    harness.tickets.send("ticket.created", ticket_body(reservation.id, USER))

    harness.ticket_consumer.run_once()

    assert harness.uow.expert_reservations.rows[reservation.id].status is RESERVED
    assert len(harness.tickets.deleted) == 1


def test_ticket_answered_is_acknowledged_without_effect(harness: Harness):
    reservation = harness.uow.expert_reservations.seed(USER)
    harness.tickets.send("ticket.answered", ticket_body(reservation.id, USER, answer_text="hi"))

    harness.ticket_consumer.run_once()

    assert harness.uow.expert_reservations.rows[reservation.id].status is RESERVED
    assert (len(harness.tickets.deleted), harness.uow.commits) == (1, 0)


def test_an_unknown_event_type_is_acknowledged_with_a_warning(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.tickets.send("ticket.reopened", {})

    with caplog.at_level(logging.WARNING, logger="app.service.consumer"):
        harness.ticket_consumer.run_once()

    assert len(harness.tickets.deleted) == 1
    assert caplog.records[0].event_type == "ticket.reopened"


@pytest.mark.parametrize(
    ("event_type", "body"),
    [
        ("order.placed", "not json"),
        ("order.placed", {"order_id": str(uuid.uuid4())}),
        ("order.placed", order_placed_body(placed_at="2026-11-11T12:00:00")),
        ("order.placed", order_placed_body(order_id="not-a-uuid")),
        (None, order_placed_body()),
    ],
    ids=["not json", "missing fields", "naive timestamp", "malformed id", "no event type"],
)
def test_a_poison_message_stays_in_the_queue_and_does_not_stop_the_batch(
    harness: Harness, caplog: pytest.LogCaptureFixture, event_type, body
):
    harness.orders.send(event_type, body)
    good = harness.orders.send("order.placed", order_placed_body())

    with caplog.at_level(logging.ERROR, logger="app.service.consumer"):
        harness.order_consumer.run_once()

    assert harness.orders.deleted == [good]
    assert len(harness.uow.placed_orders.orders) == 1
    assert [r.message for r in caplog.records] == ["message handling failed, left in the queue"]


def test_a_violated_invariant_is_not_swallowed_and_the_message_is_not_deleted(harness: Harness):
    class RejectingOrders:
        def add(self, order):
            raise RuntimeError("ck_placed_orders_total_paid_non_negative")

    harness.uow.placed_orders = RejectingOrders()
    harness.orders.send("order.placed", order_placed_body(total_paid_cents=-1))

    harness.order_consumer.run_once()

    assert harness.orders.deleted == []
    assert harness.log == []


def test_a_message_that_could_not_be_deleted_is_logged_and_the_batch_continues(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.orders.delete_fails = True
    harness.orders.send("order.placed", order_placed_body())
    harness.orders.send("order.placed", order_placed_body())

    with caplog.at_level(logging.ERROR, logger="app.service.consumer"):
        harness.order_consumer.run_once()

    assert len(harness.uow.placed_orders.orders) == 2
    assert len(caplog.records) == 2


def test_an_unreachable_queue_surfaces_to_the_background_loop(harness: Harness):
    harness.orders.receive_fails = True

    with pytest.raises(QueueUnavailable):
        harness.order_consumer.run_once()


def test_batch_size_and_wait_come_from_the_configuration(harness: Harness):
    harness.order_consumer.run_once()

    assert harness.orders.last_receive == (10, 1)


def test_an_empty_poll_receives_nothing(harness: Harness):
    assert harness.order_consumer.run_once() == 0


class RecordingRoute:
    def __init__(self) -> None:
        self.correlation_ids: list[str | None] = []

    def __call__(self, body: str) -> None:
        self.correlation_ids.append(get_correlation_id())


@pytest.mark.parametrize(
    ("attributes", "body", "expected"),
    [
        ({"correlation_id": "req-7"}, {"event_id": "evt-1"}, "req-7"),
        ({}, {"event_id": "evt-1"}, "evt-1"),
        ({}, {}, "m-0"),
    ],
    ids=["attribute", "event id", "message id"],
)
def test_each_message_is_handled_under_its_own_correlation_id(attributes, body, expected):
    queue, route = FakeQueue(), RecordingRoute()
    queue.send("order.placed", body, **attributes)
    consumer = QueueConsumer(queue, {"order.placed": route}, frozenset(), 5, 1)
    set_correlation_id("caller")

    consumer.run_once()

    assert route.correlation_ids == [expected]
    assert get_correlation_id() == "caller"


def test_a_body_that_is_not_an_object_falls_back_to_the_message_id():
    queue, route = FakeQueue(), RecordingRoute()
    queue.send("order.placed", "[1, 2]")
    consumer = QueueConsumer(queue, {"order.placed": route}, frozenset(), 5, 1)

    consumer.run_once()

    assert route.correlation_ids == ["m-0"]
