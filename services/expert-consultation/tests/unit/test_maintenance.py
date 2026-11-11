import logging
import uuid
from contextvars import copy_context
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.service.events import event_id, terminal_event
from app.service.maintenance import TicketMaintenance
from app.service.model import TicketStatus
from app.service.ticket_publisher import TerminalEventPublisher
from app.service.tickets import TicketService
from tests.unit.fakes import (
    HOUR,
    NOW,
    TTL_SECONDS,
    FakeUnitOfWork,
    RecordingEvents,
    answered_ticket,
    cancelled_ticket,
    make_ticket,
)

GRACE_SECONDS = 30.0
BATCH_SIZE = 10


class Harness:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.uow = FakeUnitOfWork(log=self.log)
        self.events = RecordingEvents(self.log)
        terminal_events = TerminalEventPublisher(lambda: self.uow, self.events)
        self.maintenance = TicketMaintenance(
            lambda: self.uow, self.events, terminal_events, GRACE_SECONDS, BATCH_SIZE
        )
        self.tickets_service = TicketService(
            lambda: self.uow, self.events, terminal_events, TTL_SECONDS
        )

    @property
    def tickets(self):
        return self.uow.tickets

    def overdue(self, **overrides: Any):
        fields = {"created_at": NOW - 80 * HOUR, "expires_at": NOW - 8 * HOUR, **overrides}
        return self.tickets.add(make_ticket(**fields))

    def run(self, step) -> Any:
        return copy_context().run(step)


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_overdue_tickets_are_cancelled_then_announced_and_marked(harness: Harness):
    ticket = harness.overdue()
    harness.tickets.add(make_ticket())

    cancelled = harness.run(harness.maintenance.expire_overdue)

    stored = harness.tickets.rows[ticket.id]
    assert cancelled == 1
    assert (stored.status, stored.cancelled_at) == (TicketStatus.CANCELLED, NOW)
    assert harness.log == ["commit", "publish ticket.cancelled", "mark published", "commit"]
    (event,) = harness.events.published
    assert (event.ticket_id, event.occurred_at) == (ticket.id, NOW)
    assert stored.terminal_event_published_at == NOW


def test_open_and_closed_tickets_are_left_alone_by_the_sweep(harness: Harness):
    open_ticket = harness.tickets.add(make_ticket())
    answered = harness.tickets.add(answered_ticket())
    cancelled = harness.tickets.add(cancelled_ticket())

    assert harness.run(harness.maintenance.expire_overdue) == 0

    assert harness.events.published == []
    assert harness.tickets.rows[open_ticket.id].status is TicketStatus.OPEN
    assert harness.tickets.rows[answered.id].status is TicketStatus.ANSWERED
    assert harness.tickets.rows[cancelled.id].terminal_event_published_at is None


def test_the_sweep_takes_a_batch_from_the_configuration(harness: Harness):
    harness.run(harness.maintenance.expire_overdue)

    assert harness.tickets.last_claim == ("expired", BATCH_SIZE)


def test_a_failed_cancellation_event_does_not_block_the_others_and_stays_for_the_republisher(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    failing = harness.overdue(expires_at=NOW - 9 * HOUR)
    healthy = harness.overdue(expires_at=NOW - 8 * HOUR)
    harness.events.failing_ticket_ids.add(failing.id)

    with caplog.at_level(logging.ERROR, logger="app.service.ticket_publisher"):
        cancelled = harness.run(harness.maintenance.expire_overdue)

    assert cancelled == 2
    assert [event.ticket_id for event in harness.events.published] == [healthy.id]
    assert harness.tickets.rows[failing.id].status is TicketStatus.CANCELLED
    assert harness.tickets.rows[failing.id].terminal_event_published_at is None
    assert harness.tickets.rows[healthy.id].terminal_event_published_at == NOW
    assert len(caplog.records) == 1


def test_each_cancellation_is_published_under_its_event_id_as_correlation_id(harness: Harness):
    ticket = harness.overdue()

    harness.run(harness.maintenance.expire_overdue)

    assert harness.events.correlation_ids == [str(event_id(ticket.id, "ticket.cancelled"))]


def test_unpublished_terminal_tickets_are_republished_after_the_grace_and_marked(harness: Harness):
    answered = harness.tickets.add(answered_ticket(answered_at=NOW - timedelta(minutes=5)))
    cancelled = harness.tickets.add(cancelled_ticket(cancelled_at=NOW - timedelta(minutes=5)))

    sent = harness.run(harness.maintenance.republish_terminal)

    assert sent == 2
    assert {event.ticket_id for event in harness.events.published} == {answered.id, cancelled.id}
    assert all(
        harness.tickets.rows[t.id].terminal_event_published_at == NOW for t in (answered, cancelled)
    )
    assert harness.uow.commits == 1


def test_a_terminal_change_inside_the_grace_is_not_republished(harness: Harness):
    harness.tickets.add(answered_ticket(answered_at=NOW - timedelta(seconds=10)))
    harness.tickets.add(cancelled_ticket(cancelled_at=NOW - timedelta(seconds=GRACE_SECONDS - 1)))

    assert harness.run(harness.maintenance.republish_terminal) == 0
    assert harness.events.published == []
    assert harness.tickets.last_claim == ("unpublished", GRACE_SECONDS, BATCH_SIZE)


def test_published_and_open_tickets_are_not_republished(harness: Harness):
    harness.tickets.add(
        answered_ticket(
            answered_at=NOW - HOUR, terminal_event_published_at=NOW - timedelta(minutes=50)
        )
    )
    harness.tickets.add(make_ticket())

    assert harness.run(harness.maintenance.republish_terminal) == 0


def test_a_republished_answer_is_identical_to_the_original(harness: Harness):
    ticket = harness.tickets.add(make_ticket())
    harness.tickets_service.answer(ticket.id, uuid.uuid4(), "Water less.")
    original = harness.events.published[0]
    # The process died after the publish and before the record; later the grace has passed.
    harness.tickets.rows[ticket.id] = _unmarked(harness.tickets.rows[ticket.id])
    harness.tickets.now = NOW + HOUR

    harness.run(harness.maintenance.republish_terminal)

    republished = harness.events.published[1]
    assert republished == original
    assert republished.event_id == event_id(ticket.id, "ticket.answered")
    assert republished.occurred_at == NOW
    assert republished.answer_text == "Water less."


def test_a_republished_cancellation_is_identical_to_the_original(harness: Harness):
    ticket = harness.overdue()
    harness.run(harness.maintenance.expire_overdue)
    original = harness.events.published[0]
    harness.tickets.rows[ticket.id] = _unmarked(harness.tickets.rows[ticket.id])
    harness.tickets.now = NOW + HOUR

    harness.run(harness.maintenance.republish_terminal)

    assert (
        harness.events.published[1] == original == terminal_event(harness.tickets.rows[ticket.id])
    )


def test_each_resend_is_logged_with_the_event_id_as_correlation_id(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    ticket = harness.tickets.add(cancelled_ticket(cancelled_at=NOW - HOUR))

    with caplog.at_level(logging.INFO, logger="app.service.maintenance"):
        harness.run(harness.maintenance.republish_terminal)

    expected = str(event_id(ticket.id, "ticket.cancelled"))
    assert harness.events.correlation_ids == [expected]
    (record,) = caplog.records
    assert (record.ticket_id, record.event_type) == (str(ticket.id), "ticket.cancelled")


def test_a_failing_resend_stays_unmarked_and_does_not_block_the_next(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    failing = harness.tickets.add(cancelled_ticket(cancelled_at=NOW - HOUR))
    healthy = harness.tickets.add(answered_ticket(answered_at=NOW - HOUR))
    harness.events.failing_ticket_ids.add(failing.id)

    with caplog.at_level(logging.ERROR, logger="app.service.maintenance"):
        sent = harness.run(harness.maintenance.republish_terminal)

    assert sent == 1
    assert harness.tickets.rows[failing.id].terminal_event_published_at is None
    assert harness.tickets.rows[healthy.id].terminal_event_published_at == NOW
    assert len(caplog.records) == 1


def test_one_run_expires_and_then_republishes(harness: Harness):
    overdue = harness.overdue()
    lost = harness.tickets.add(answered_ticket(answered_at=NOW - HOUR))

    harness.run(harness.maintenance.run_once)

    assert {event.ticket_id for event in harness.events.published} == {overdue.id, lost.id}
    assert harness.events.types.count("ticket.cancelled") == 1


def _unmarked(ticket):
    return replace(ticket, terminal_event_published_at=None)
