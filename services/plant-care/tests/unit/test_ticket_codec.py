import json
import uuid
from datetime import UTC, datetime

import pytest

from app.messaging.ticket_codec import (
    ANSWER_LIMIT,
    decode_ticket_answered,
    decode_ticket_cancelled,
    decode_ticket_created,
    ticket_routes,
)
from app.service.consumer import InvalidMessage, QueueConsumer
from app.service.ticket_events import TicketAnswered, TicketCancelled, TicketCreated
from app.service.ticket_handlers import TicketEventHandlers
from tests.unit.fake_queue import FakeQueue
from tests.unit.fakes import NOW
from tests.unit.ticket_fakes import TicketUnitOfWork

TICKET = uuid.uuid4()
ESCALATION = uuid.uuid4()
USER = uuid.uuid4()


def body(**overrides) -> str:
    fields = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": NOW.isoformat(),
        "ticket_id": str(TICKET),
        "escalation_id": str(ESCALATION),
        "user_id": str(USER),
    }
    return json.dumps({**fields, **overrides})


def answered_body(**overrides) -> str:
    fields = {
        "journal_id": str(uuid.uuid4()),
        "answer_text": "Repot it. Мягко.",
        "answered_at": "2026-11-12T09:00:00+00:00",
    }
    return body(**{**fields, **overrides})


def test_ticket_created_is_decoded():
    assert decode_ticket_created(body()) == TicketCreated(ESCALATION, TICKET)


def test_ticket_cancelled_is_decoded():
    assert decode_ticket_cancelled(body()) == TicketCancelled(ESCALATION, TICKET)


def test_ticket_answered_is_decoded_with_the_text_and_the_time():
    assert decode_ticket_answered(answered_body()) == TicketAnswered(
        ESCALATION, TICKET, "Repot it. Мягко.", datetime(2026, 11, 12, 9, tzinfo=UTC)
    )


def test_unknown_fields_are_ignored():
    assert decode_ticket_created(body(extra="x")) == TicketCreated(ESCALATION, TICKET)


@pytest.mark.parametrize("decode", [decode_ticket_created, decode_ticket_cancelled])
@pytest.mark.parametrize("missing", ["ticket_id", "escalation_id"])
def test_a_body_without_its_ids_is_invalid(decode, missing: str):
    fields = json.loads(body())
    del fields[missing]

    with pytest.raises(InvalidMessage):
        decode(json.dumps(fields))


@pytest.mark.parametrize(
    "overrides",
    [
        {"answer_text": ""},
        {"answer_text": "x" * (ANSWER_LIMIT + 1)},
        {"answer_text": None},
        {"answered_at": "2026-11-12T09:00:00"},
        {"answered_at": "yesterday"},
        {"escalation_id": "not-a-uuid"},
    ],
)
def test_an_answered_body_that_breaks_the_contract_is_invalid(overrides: dict):
    with pytest.raises(InvalidMessage):
        decode_ticket_answered(answered_body(**overrides))


def test_an_answer_of_the_maximum_length_is_valid():
    event = decode_ticket_answered(answered_body(answer_text="я" * ANSWER_LIMIT))

    assert len(event.answer_text) == ANSWER_LIMIT


def test_malformed_json_is_invalid():
    with pytest.raises(InvalidMessage):
        decode_ticket_created("{")


def test_routes_cover_exactly_the_three_ticket_events():
    handlers = TicketEventHandlers(lambda: TicketUnitOfWork())

    assert set(ticket_routes(handlers)) == {"ticket.created", "ticket.answered", "ticket.cancelled"}


class ConsumerSetup:
    def __init__(self) -> None:
        self.uow = TicketUnitOfWork()
        journal = self.uow.journals.seed(USER)
        update = self.uow.updates.seed(journal.id)
        self.escalation = self.uow.escalations.seed(update.id)
        self.queue = FakeQueue()
        self.consumer = QueueConsumer(
            self.queue,
            ticket_routes(TicketEventHandlers(lambda: self.uow)),
            frozenset(),
            5,
            0,
        )

    def message(self, event_type: str, **fields):
        text = answered_body(**fields) if event_type == "ticket.answered" else body(**fields)
        return self.queue.send(event_type, text.replace(str(ESCALATION), str(self.escalation.id)))


def test_the_message_is_deleted_only_after_the_handler_committed():
    setup = ConsumerSetup()
    log: list[str] = []
    setup.queue.log = log
    commit = setup.uow.commit
    setup.uow.commit = lambda: (log.append("commit"), commit())[1]
    message = setup.message("ticket.created")

    setup.consumer.run_once()

    assert log == ["commit", "delete"]
    assert setup.queue.deleted == [message]
    assert setup.uow.escalations.rows[setup.escalation.id].ticket_id == TICKET


def test_a_failing_commit_leaves_the_message_for_redelivery():
    setup = ConsumerSetup()
    setup.uow.fail_commit_with = RuntimeError("db down")
    setup.message("ticket.answered")

    setup.consumer.run_once()

    assert setup.queue.deleted == []


def test_an_invalid_message_is_left_for_the_dlq():
    setup = ConsumerSetup()
    setup.queue.send("ticket.answered", answered_body(answer_text=""))

    setup.consumer.run_once()

    assert setup.queue.deleted == []


def test_a_duplicate_message_is_acknowledged_without_a_change():
    setup = ConsumerSetup()
    setup.message("ticket.created")
    setup.message("ticket.created")

    setup.consumer.run_once()

    assert len(setup.queue.deleted) == 2


def test_the_correlation_id_of_the_message_attribute_is_used(caplog: pytest.LogCaptureFixture):
    setup = ConsumerSetup()
    setup.queue.send("ticket.created", body(), correlation_id="req-9")

    with caplog.at_level("INFO"):
        setup.consumer.run_once()

    assert any(r.message.startswith("ticket.created") for r in caplog.records)
