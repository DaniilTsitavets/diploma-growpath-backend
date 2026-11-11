import logging
import uuid
from datetime import timedelta

import pytest

from app.service.events import created_event, terminal_event
from app.service.model import TicketStatus
from app.service.ticket_errors import (
    PublishFailed,
    TicketAlreadyAnswered,
    TicketExpired,
    TicketNotFound,
)
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
    escalation_request,
    make_ticket,
)


class Harness:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.uow = FakeUnitOfWork(log=self.log)
        self.events = RecordingEvents(self.log)
        self.service = TicketService(
            lambda: self.uow,
            self.events,
            TerminalEventPublisher(lambda: self.uow, self.events),
            TTL_SECONDS,
        )

    @property
    def tickets(self):
        return self.uow.tickets

    def only_ticket(self):
        (ticket,) = self.tickets.rows.values()
        return ticket


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_an_escalation_inside_its_window_becomes_an_open_ticket_that_expires_after_the_ttl(
    harness: Harness,
):
    request = escalation_request()

    harness.service.create_from_escalation(request)

    ticket = harness.only_ticket()
    assert ticket.status is TicketStatus.OPEN
    assert (ticket.escalation_id, ticket.user_id) == (request.escalation_id, request.user_id)
    assert (ticket.journal_id, ticket.update_id) == (request.journal_id, request.update_id)
    assert ticket.context == request.context
    assert ticket.created_at == NOW
    assert ticket.expires_at == NOW + timedelta(seconds=TTL_SECONDS)


def test_the_created_event_goes_out_only_after_the_commit(harness: Harness):
    harness.service.create_from_escalation(escalation_request())

    assert harness.log == ["commit", "publish ticket.created"]
    assert harness.events.published == [created_event(harness.only_ticket())]


def test_a_failed_publish_leaves_the_message_unacknowledged_and_the_ticket_committed(
    harness: Harness,
):
    request = escalation_request()
    harness.events.failing = True

    with pytest.raises(PublishFailed):
        harness.service.create_from_escalation(request)

    assert harness.uow.commits == 1
    assert harness.only_ticket().escalation_id == request.escalation_id


def test_the_redelivery_after_a_failed_publish_republishes_the_identical_event(harness: Harness):
    request = escalation_request()
    harness.events.failing = True
    with pytest.raises(PublishFailed):
        harness.service.create_from_escalation(request)
    harness.events.failing = False

    harness.service.create_from_escalation(request)

    assert len(harness.tickets.rows) == 1
    assert harness.events.published == [created_event(harness.only_ticket())]


def test_a_redelivery_of_an_open_ticket_republishes_ticket_created_and_creates_nothing(
    harness: Harness,
):
    request = escalation_request()
    harness.service.create_from_escalation(request)
    first = harness.events.published[0]

    harness.service.create_from_escalation(request)

    assert len(harness.tickets.rows) == 1
    assert harness.uow.commits == 1
    assert harness.events.published == [first, first]


def test_a_redelivery_after_accept_until_still_republishes_for_an_accepted_ticket(
    harness: Harness,
):
    request = escalation_request(accept_until=NOW + HOUR)
    harness.service.create_from_escalation(request)
    harness.tickets.now = NOW + 2 * HOUR

    harness.service.create_from_escalation(request)

    assert harness.events.types == ["ticket.created", "ticket.created"]


def test_a_redelivery_of_an_answered_ticket_also_republishes_the_answer_and_marks_it(
    harness: Harness,
):
    ticket = harness.tickets.add(answered_ticket())
    request = escalation_request(escalation_id=ticket.escalation_id)

    harness.service.create_from_escalation(request)

    assert harness.events.published == [created_event(ticket), terminal_event(ticket)]
    assert harness.tickets.rows[ticket.id].terminal_event_published_at == NOW


def test_a_redelivery_of_an_expired_ticket_also_republishes_the_cancellation(harness: Harness):
    ticket = harness.tickets.add(cancelled_ticket())
    request = escalation_request(escalation_id=ticket.escalation_id)

    harness.service.create_from_escalation(request)

    assert harness.events.types == ["ticket.created", "ticket.cancelled"]
    assert harness.tickets.rows[ticket.id].terminal_event_published_at == NOW


def test_a_failed_terminal_republish_on_redelivery_is_left_for_the_next_delivery(harness: Harness):
    ticket = harness.tickets.add(cancelled_ticket())
    harness.events.failing_ticket_ids.add(ticket.id)

    with pytest.raises(PublishFailed):
        harness.service.create_from_escalation(
            escalation_request(escalation_id=ticket.escalation_id)
        )

    assert harness.tickets.rows[ticket.id].terminal_event_published_at is None


def test_an_escalation_past_its_window_creates_no_ticket_and_is_logged(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    request = escalation_request(accept_until=NOW - timedelta(seconds=1))

    with caplog.at_level(logging.INFO, logger="app.service.tickets"):
        harness.service.create_from_escalation(request)

    assert harness.tickets.rows == {}
    assert harness.events.published == []
    (record,) = caplog.records
    assert record.escalation_id == str(request.escalation_id)
    assert "acceptance window" in record.message


def test_the_window_closes_exactly_at_accept_until(harness: Harness):
    harness.service.create_from_escalation(escalation_request(accept_until=NOW))

    assert harness.tickets.rows == {}


def test_an_invariant_violation_propagates_so_the_message_reaches_the_dlq(harness: Harness):
    harness.tickets.create_error = RuntimeError("uq_tickets_active_update")

    with pytest.raises(RuntimeError, match="uq_tickets_active_update"):
        harness.service.create_from_escalation(escalation_request())

    assert harness.events.published == []


def test_an_answer_closes_the_ticket_and_announces_it_after_the_commit(harness: Harness):
    ticket = harness.tickets.add(make_ticket())
    expert = uuid.uuid4()

    result = harness.service.answer(ticket.id, expert, "Water less.")

    stored = harness.tickets.rows[ticket.id]
    assert result.created is True
    assert (result.ticket.id, result.ticket.answer) == (ticket.id, stored.answer)
    assert (stored.status, stored.answered_at) == (TicketStatus.ANSWERED, NOW)
    assert (stored.answer.expert_id, stored.answer.body) == (expert, "Water less.")
    assert harness.log == ["commit", "publish ticket.answered", "mark published", "commit"]
    assert harness.events.published == [terminal_event(stored)]
    assert stored.terminal_event_published_at == NOW


def test_the_answered_event_carries_the_text_and_the_journal(harness: Harness):
    ticket = harness.tickets.add(make_ticket())

    harness.service.answer(ticket.id, uuid.uuid4(), "Repot it.")

    (event,) = harness.events.published
    assert (event.answer_text, event.journal_id, event.answered_at) == (
        "Repot it.",
        ticket.journal_id,
        NOW,
    )


def test_the_same_expert_repeating_the_answer_gets_the_stored_one_and_the_event_again(
    harness: Harness,
):
    ticket = harness.tickets.add(make_ticket())
    expert = uuid.uuid4()
    harness.service.answer(ticket.id, expert, "Water less.")
    harness.tickets.now = NOW + HOUR

    repeated = harness.service.answer(ticket.id, expert, "Another text.")

    assert repeated.created is False
    assert repeated.ticket.answer.body == "Water less."
    assert repeated.ticket.answered_at == NOW
    assert harness.events.published[0] == harness.events.published[1]
    assert harness.tickets.rows[ticket.id].answer.body == "Water less."


def test_another_expert_cannot_answer_an_answered_ticket(harness: Harness):
    ticket = harness.tickets.add(answered_ticket())

    with pytest.raises(TicketAlreadyAnswered):
        harness.service.answer(ticket.id, uuid.uuid4(), "Mine is better.")

    assert harness.events.published == []
    assert harness.tickets.rows[ticket.id].answer.body == "Water less."


@pytest.mark.parametrize(
    "ticket",
    [
        cancelled_ticket(),
        make_ticket(created_at=NOW - 80 * HOUR, expires_at=NOW - 8 * HOUR),
        make_ticket(created_at=NOW - HOUR, expires_at=NOW),
    ],
    ids=["cancelled", "overdue but not yet swept", "deadline this instant"],
)
def test_an_expired_ticket_cannot_be_answered(harness: Harness, ticket):
    harness.tickets.add(ticket)

    with pytest.raises(TicketExpired):
        harness.service.answer(ticket.id, uuid.uuid4(), "Too late.")

    assert harness.events.published == []
    assert harness.tickets.rows[ticket.id].answer is None


def test_an_unknown_ticket_is_not_found(harness: Harness):
    with pytest.raises(TicketNotFound):
        harness.service.answer(uuid.uuid4(), uuid.uuid4(), "Hello?")


def test_a_failed_publish_after_an_answer_keeps_the_answer_for_the_republisher(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    ticket = harness.tickets.add(make_ticket())
    harness.events.failing = True

    with caplog.at_level(logging.ERROR, logger="app.service.ticket_publisher"):
        result = harness.service.answer(ticket.id, uuid.uuid4(), "Water less.")

    stored = harness.tickets.rows[ticket.id]
    assert result.created is True
    assert stored.status is TicketStatus.ANSWERED
    assert stored.terminal_event_published_at is None
    assert [record.message for record in caplog.records] == [
        "terminal ticket event not published, the republisher will retry"
    ]


def test_a_retry_whose_publish_fails_is_still_accepted(harness: Harness):
    ticket = harness.tickets.add(make_ticket())
    expert = uuid.uuid4()
    harness.service.answer(ticket.id, expert, "Water less.")
    harness.events.failing = True

    repeated = harness.service.answer(ticket.id, expert, "Water less.")

    assert repeated.created is False
