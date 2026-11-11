import logging
import uuid

import pytest
from growpath_observability import get_correlation_id, set_correlation_id

from app.messaging.codec import order_routes
from app.service.consumer import QueueConsumer, QueueUnavailable
from app.service.event_handlers import EventHandlers
from tests.unit.fake_queue import FakeQueue, order_placed_body
from tests.unit.fakes import FakeUnitOfWork

USER = uuid.uuid4()


class Harness:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.uow = FakeUnitOfWork()
        self.queue = FakeQueue(self.log)
        original_commit = self.uow.commit

        def commit() -> None:
            original_commit()
            self.log.append("commit")

        self.uow.commit = commit
        self.consumer = QueueConsumer(
            self.queue,
            order_routes(EventHandlers(lambda: self.uow)),
            frozenset(),
            batch_size=10,
            wait_seconds=1,
        )


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_a_pack_order_opens_a_journal_and_is_deleted_only_after_the_commit(harness: Harness):
    body = order_placed_body(user_id=str(USER), plant_species="Lavender")
    harness.queue.send("order.placed", body)

    received = harness.consumer.run_once()

    (journal,) = harness.uow.journals.rows.values()
    assert received == 1
    assert (journal.user_id, journal.species, journal.planted_on) == (USER, "Lavender", None)
    assert str(journal.order_id) == body["order_id"]
    assert harness.log == ["commit", "delete"]


def test_a_sensor_order_changes_nothing_and_is_acknowledged(harness: Harness):
    harness.queue.send(
        "order.placed", order_placed_body(item_type="sensor", plant_species=None, seed_count=None)
    )

    harness.consumer.run_once()

    assert harness.uow.journals.rows == {}
    assert harness.uow.commits == 0
    assert len(harness.queue.deleted) == 1


def test_a_redelivered_order_keeps_one_journal_and_is_still_acknowledged(harness: Harness):
    body = order_placed_body()
    harness.queue.send("order.placed", body)
    harness.queue.send("order.placed", body)

    harness.consumer.run_once()

    assert len(harness.uow.journals.rows) == 1
    assert len(harness.queue.deleted) == 2


def test_a_pack_without_a_species_stays_in_the_queue_for_the_dlq(harness: Harness):
    harness.queue.send("order.placed", order_placed_body(plant_species=None))

    harness.consumer.run_once()

    assert harness.uow.journals.rows == {}
    assert harness.queue.deleted == []


def test_a_message_outside_the_contract_stays_in_the_queue(harness: Harness):
    harness.queue.send("order.placed", "not json")

    harness.consumer.run_once()

    assert harness.queue.deleted == []


def test_a_failing_commit_leaves_the_message_in_the_queue(harness: Harness):
    harness.uow.fail_commit_with = RuntimeError("constraint violated")
    harness.queue.send("order.placed", order_placed_body())

    harness.consumer.run_once()

    assert harness.queue.deleted == []


def test_a_message_without_an_event_type_stays_in_the_queue(harness: Harness):
    harness.queue.send(None, order_placed_body())

    harness.consumer.run_once()

    assert harness.queue.deleted == []


def test_an_unknown_event_type_is_acknowledged_with_a_warning(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.queue.send("garden.opened", {})

    with caplog.at_level(logging.WARNING, logger="app.service.consumer"):
        harness.consumer.run_once()

    assert len(harness.queue.deleted) == 1
    assert [record.message for record in caplog.records] == [
        "event of an unknown type acknowledged"
    ]


def test_a_failed_delete_is_logged_and_does_not_stop_the_batch(harness: Harness):
    harness.queue.delete_fails = True
    harness.queue.send("order.placed", order_placed_body())
    harness.queue.send("order.placed", order_placed_body())

    assert harness.consumer.run_once() == 2

    assert len(harness.uow.journals.rows) == 2


def test_an_unreachable_queue_raises_to_the_background_loop(harness: Harness):
    harness.queue.receive_fails = True

    with pytest.raises(QueueUnavailable):
        harness.consumer.run_once()


def test_the_correlation_id_of_the_message_is_used_while_handling_it(harness: Harness):
    seen: list[str | None] = []
    harness.consumer = QueueConsumer(
        harness.queue,
        {"order.placed": lambda body: seen.append(get_correlation_id())},
        frozenset(),
        10,
        1,
    )
    harness.queue.send("order.placed", order_placed_body(), correlation_id="req-7")
    set_correlation_id("outer")

    harness.consumer.run_once()

    assert seen == ["req-7"]
    assert get_correlation_id() == "outer"


def test_the_event_id_is_the_correlation_id_when_the_message_has_none(harness: Harness):
    seen: list[str | None] = []
    harness.consumer = QueueConsumer(
        harness.queue,
        {"order.placed": lambda body: seen.append(get_correlation_id())},
        frozenset(),
        10,
        1,
    )
    body = order_placed_body()
    harness.queue.send("order.placed", body)

    harness.consumer.run_once()

    assert seen == [body["event_id"]]
