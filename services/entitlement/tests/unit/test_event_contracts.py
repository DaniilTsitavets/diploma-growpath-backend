import json

import pytest

from app.messaging.codec import (
    TICKET_EVENTS_IGNORED,
    decode_order_placed,
    decode_ticket_cancelled,
    decode_ticket_created,
    ticket_routes,
)
from app.service.errors import InvalidMessage
from tests.unit.event_contracts import (
    example_payloads,
    load_asyncapi,
    payload_errors,
    required_fields,
)

ORDER_FIELDS_READ = {"order_id", "user_id", "total_paid_cents", "placed_at"}
TICKET_FIELDS_READ = {"escalation_id", "user_id"}
# message -> (codec, the fields the codec needs); the rest of the schema is carried but not read.
CONSUMED = {
    "OrderPlaced": (decode_order_placed, ORDER_FIELDS_READ),
    "TicketCreated": (decode_ticket_created, TICKET_FIELDS_READ),
    "TicketCancelled": (decode_ticket_cancelled, TICKET_FIELDS_READ),
}
EXAMPLES = [(message, name) for message in CONSUMED for name in example_payloads(message)]


def accepted(codec, payload: dict) -> bool:
    try:
        codec(json.dumps(payload))
    except InvalidMessage:
        return False
    return True


@pytest.mark.parametrize(("message", "name"), EXAMPLES)
def test_the_contract_examples_satisfy_the_contract(message: str, name: str):
    assert payload_errors(message, example_payloads(message)[name]) == []


@pytest.mark.parametrize(("message", "name"), EXAMPLES)
def test_the_codec_accepts_the_contract_examples(message: str, name: str):
    codec, _ = CONSUMED[message]

    assert accepted(codec, example_payloads(message)[name])


@pytest.mark.parametrize("message", CONSUMED)
def test_the_codec_rejects_exactly_the_payloads_that_lack_a_field_it_reads(message: str):
    codec, read = CONSUMED[message]
    example = next(iter(example_payloads(message).values()))

    rejected = set()
    for field in required_fields(message):
        broken = {key: value for key, value in example.items() if key != field}
        assert payload_errors(message, broken), f"the contract lets {field} be missing"
        if not accepted(codec, broken):
            rejected.add(field)

    assert rejected == read


@pytest.mark.parametrize("message", CONSUMED)
def test_the_codec_rejects_a_malformed_user_id(message: str):
    codec, _ = CONSUMED[message]
    payload = {**next(iter(example_payloads(message).values())), "user_id": "not-a-uuid"}

    assert payload_errors(message, payload)
    assert not accepted(codec, payload)


@pytest.mark.parametrize("message", CONSUMED)
def test_the_codec_ignores_fields_the_contract_may_add(message: str):
    codec, _ = CONSUMED[message]
    payload = {**next(iter(example_payloads(message).values())), "added_later": 1}

    assert accepted(codec, payload)


def test_the_ticket_queue_gets_exactly_the_events_the_service_handles():
    channel = load_asyncapi()["channels"]["entitlementTicketEventsQueue"]
    filtered = set(channel["x-sns-subscription"]["filterPolicy"]["event_type"])
    contract_messages = {
        message["$ref"].rsplit("/", 1)[1] for message in channel["messages"].values()
    }

    assert filtered == set(ticket_routes(object()))
    assert contract_messages == {"TicketCreated", "TicketCancelled"}
    assert TICKET_EVENTS_IGNORED == {"ticket.answered"}
