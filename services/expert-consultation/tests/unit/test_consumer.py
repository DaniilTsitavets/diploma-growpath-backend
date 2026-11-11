import json
import logging

import pytest
from growpath_observability import get_correlation_id

from app.messaging.escalation_codec import escalation_routes
from app.service.consumer import QueueConsumer
from app.service.ticket_publisher import TerminalEventPublisher
from app.service.tickets import TicketService
from tests.unit.fake_queue import FakeQueue
from tests.unit.fakes import (
    HOUR,
    NOW,
    TTL_SECONDS,
    FakeUnitOfWork,
    RecordingEvents,
    cancelled_ticket,
    escalation_body,
)

BATCH_SIZE = 7
WAIT_SECONDS = 3


class Harness:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.uow = FakeUnitOfWork(log=self.log)
        self.events = RecordingEvents(self.log)
        self.queue = FakeQueue(self.log)
        service = TicketService(
            lambda: self.uow,
            self.events,
            TerminalEventPublisher(lambda: self.uow, self.events),
            TTL_SECONDS,
        )
        self.consumer = QueueConsumer(
            self.queue, escalation_routes(service), BATCH_SIZE, WAIT_SECONDS
        )


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_a_ticket_is_created_published_and_only_then_the_message_is_deleted(harness: Harness):
    body = escalation_body()
    harness.queue.send("escalation.requested", body)

    received = harness.consumer.run_once()

    (ticket,) = harness.uow.tickets.rows.values()
    assert received == 1
    assert str(ticket.escalation_id) == body["escalation_id"]
    assert harness.log == ["commit", "publish ticket.created", "delete"]


def test_a_failed_publish_leaves_the_message_in_the_queue(harness: Harness):
    harness.queue.send("escalation.requested", escalation_body())
    harness.events.failing = True

    harness.consumer.run_once()

    assert harness.queue.deleted == []
    assert len(harness.uow.tickets.rows) == 1


def test_the_redelivery_after_a_failed_publish_publishes_and_deletes(harness: Harness):
    harness.queue.send("escalation.requested", escalation_body())
    harness.events.failing = True
    harness.consumer.run_once()
    harness.events.failing = False

    harness.consumer.run_once()

    assert harness.events.types == ["ticket.created"]
    assert len(harness.queue.deleted) == 1
    assert len(harness.uow.tickets.rows) == 1


def test_an_event_past_its_window_is_acknowledged_without_a_ticket(harness: Harness):
    harness.queue.send(
        "escalation.requested", escalation_body(accept_until=(NOW - HOUR).isoformat())
    )

    harness.consumer.run_once()

    assert harness.uow.tickets.rows == {}
    assert harness.events.published == []
    assert len(harness.queue.deleted) == 1


def test_an_invariant_violation_is_left_for_the_dlq_and_logged(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.queue.send("escalation.requested", escalation_body())
    harness.uow.tickets.create_error = RuntimeError("uq_tickets_active_update")

    with caplog.at_level(logging.ERROR, logger="app.service.consumer"):
        harness.consumer.run_once()

    assert harness.queue.deleted == []
    assert [record.message for record in caplog.records] == [
        "message handling failed, left in the queue"
    ]


@pytest.mark.parametrize(
    "body",
    ["{not json", json.dumps({"escalation_id": "x"}), json.dumps(escalation_body(context={}))],
    ids=["not json", "missing fields", "no schema version"],
)
def test_a_message_that_breaks_the_contract_is_left_for_the_dlq(harness: Harness, body: str):
    harness.queue.send("escalation.requested", body)

    harness.consumer.run_once()

    assert harness.queue.deleted == []
    assert harness.uow.tickets.rows == {}


def test_a_message_without_an_event_type_is_left_for_the_dlq(harness: Harness):
    harness.queue.send(None, escalation_body())

    harness.consumer.run_once()

    assert harness.queue.deleted == []


def test_an_unknown_event_type_is_acknowledged_with_a_warning(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.queue.send("order.placed", {})

    with caplog.at_level(logging.WARNING, logger="app.service.consumer"):
        harness.consumer.run_once()

    assert len(harness.queue.deleted) == 1
    assert [record.message for record in caplog.records] == [
        "event of an unknown type acknowledged"
    ]


def test_a_failed_delete_is_logged_and_the_redelivery_republishes(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.queue.send("escalation.requested", escalation_body())
    harness.queue.delete_fails = True

    with caplog.at_level(logging.ERROR, logger="app.service.consumer"):
        harness.consumer.run_once()
    harness.queue.delete_fails = False
    harness.consumer.run_once()

    assert [record.message for record in caplog.records] == [
        "message was handled but not deleted, it will be redelivered"
    ]
    assert harness.events.types == ["ticket.created", "ticket.created"]
    assert len(harness.uow.tickets.rows) == 1


def test_a_redelivery_of_an_expired_ticket_republishes_its_cancellation(harness: Harness):
    ticket = harness.uow.tickets.add(cancelled_ticket())
    harness.queue.send(
        "escalation.requested", escalation_body(escalation_id=str(ticket.escalation_id))
    )

    harness.consumer.run_once()

    assert harness.events.types == ["ticket.created", "ticket.cancelled"]
    assert len(harness.queue.deleted) == 1


def test_the_poll_uses_the_configured_batch_and_wait(harness: Harness):
    harness.consumer.run_once()

    assert harness.queue.last_receive == (BATCH_SIZE, WAIT_SECONDS)


def test_the_correlation_id_of_the_message_follows_the_events_it_causes(harness: Harness):
    harness.queue.send("escalation.requested", escalation_body(), correlation_id="req-7")

    harness.consumer.run_once()

    assert harness.events.correlation_ids == ["req-7"]


def test_without_a_correlation_attribute_the_event_id_is_used(harness: Harness):
    body = escalation_body()
    harness.queue.send("escalation.requested", body)

    harness.consumer.run_once()

    assert harness.events.correlation_ids == [body["event_id"]]


@pytest.mark.parametrize("body", ["not json at all", "[]", "{}"], ids=["text", "array", "no id"])
def test_without_either_the_message_id_is_used(harness: Harness, body: str):
    seen: list[str | None] = []
    consumer = QueueConsumer(
        harness.queue, {"probe": lambda _: seen.append(get_correlation_id())}, 5, 0
    )
    message = harness.queue.send("probe", body)

    consumer.run_once()

    assert seen == [message.message_id]


def test_the_correlation_id_does_not_leak_between_messages(harness: Harness):
    harness.queue.send("escalation.requested", escalation_body(), correlation_id="req-1")

    harness.consumer.run_once()

    assert get_correlation_id() != "req-1"
