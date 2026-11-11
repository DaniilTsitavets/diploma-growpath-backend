"""The published ticket events against what their consumers decode.

The field lists below are copied from the consumers' codecs (plant-care `ticket_codec.py`,
entitlement `codec.py`) and from the shared contract in docs/db/README.md. A change of an event
here that a consumer cannot read fails this test instead of reaching the dead-letter queue.
"""

import json
import uuid
from typing import Annotated

import pytest
from pydantic import AwareDatetime, ConfigDict, Field, ValidationError, create_model

from app.messaging.sns import encode_ticket_event
from app.service.events import created_event, terminal_event
from tests.unit.fakes import answered_ticket, cancelled_ticket, make_ticket

ANSWER_TEXT = Annotated[str, Field(min_length=1, max_length=5000)]

SHARED_CONTRACT = {
    "ticket.created": {"event_id", "occurred_at", "ticket_id", "escalation_id", "user_id"},
    "ticket.answered": {
        "event_id",
        "occurred_at",
        "ticket_id",
        "escalation_id",
        "user_id",
        "journal_id",
        "answer_text",
        "answered_at",
    },
    "ticket.cancelled": {"event_id", "occurred_at", "ticket_id", "escalation_id", "user_id"},
}
CONSUMERS = {
    "plant-care": {
        "ticket.created": {"ticket_id": uuid.UUID, "escalation_id": uuid.UUID},
        "ticket.answered": {
            "ticket_id": uuid.UUID,
            "escalation_id": uuid.UUID,
            "answer_text": ANSWER_TEXT,
            "answered_at": AwareDatetime,
        },
        "ticket.cancelled": {"ticket_id": uuid.UUID, "escalation_id": uuid.UUID},
    },
    "entitlement": {
        "ticket.created": {"escalation_id": uuid.UUID, "user_id": uuid.UUID},
        "ticket.cancelled": {"escalation_id": uuid.UUID, "user_id": uuid.UUID},
    },
}
# What entitlement acknowledges without reading: the expert quota is committed at ticket.created.
IGNORED_BY_ENTITLEMENT = {"ticket.answered"}


def published_events() -> dict[str, str]:
    return {
        "ticket.created": encode_ticket_event(created_event(make_ticket())),
        "ticket.answered": encode_ticket_event(terminal_event(answered_ticket())),
        "ticket.cancelled": encode_ticket_event(terminal_event(cancelled_ticket())),
    }


def consumer_model(consumer: str, event_type: str):
    fields = {name: (kind, ...) for name, kind in CONSUMERS[consumer][event_type].items()}
    return create_model(f"{consumer}-{event_type}", __config__=ConfigDict(extra="ignore"), **fields)


CONSUMED = [
    (consumer, event_type) for consumer, events in CONSUMERS.items() for event_type in events
]


@pytest.mark.parametrize("event_type", SHARED_CONTRACT)
def test_each_event_carries_exactly_the_fields_of_the_shared_contract(event_type: str):
    body = json.loads(published_events()[event_type])

    assert set(body) == SHARED_CONTRACT[event_type]


@pytest.mark.parametrize(("consumer", "event_type"), CONSUMED)
def test_the_consumer_decodes_the_published_event(consumer: str, event_type: str):
    body = published_events()[event_type]

    decoded = consumer_model(consumer, event_type).model_validate_json(body)

    assert decoded is not None


@pytest.mark.parametrize(("consumer", "event_type"), CONSUMED)
def test_the_consumer_needs_nothing_the_event_lacks(consumer: str, event_type: str):
    required = set(CONSUMERS[consumer][event_type])

    assert required <= SHARED_CONTRACT[event_type]


def test_every_consumed_event_type_is_one_this_service_publishes():
    consumed = {event_type for events in CONSUMERS.values() for event_type in events}

    assert consumed | IGNORED_BY_ENTITLEMENT == set(SHARED_CONTRACT)


def test_the_consumer_reads_the_values_of_the_event_not_only_its_shape():
    ticket = answered_ticket()
    body = encode_ticket_event(terminal_event(ticket))

    decoded = consumer_model("plant-care", "ticket.answered").model_validate_json(body)

    assert decoded.ticket_id == ticket.id
    assert decoded.escalation_id == ticket.escalation_id
    assert decoded.answer_text == ticket.answer.body
    assert decoded.answered_at == ticket.answered_at


def test_a_consumer_would_reject_an_answered_event_without_its_text():
    body = json.loads(published_events()["ticket.answered"])
    del body["answer_text"]

    with pytest.raises(ValidationError):
        consumer_model("plant-care", "ticket.answered").model_validate(body)
