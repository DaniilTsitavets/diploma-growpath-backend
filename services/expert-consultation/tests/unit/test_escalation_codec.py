import json
import uuid

import pytest

from app.messaging.escalation_codec import (
    CONTEXT_LIMIT_BYTES,
    context_size,
    decode_escalation_requested,
    escalation_routes,
)
from app.service.queue import InvalidMessage
from tests.unit.fakes import HOUR, NOW, escalation_body

BASE_CONTEXT = {"schema_version": 1, "pad": ""}


def encode(**overrides) -> str:
    return json.dumps(escalation_body(**overrides))


def context_of_size(octets: int, filler: str = "a") -> dict:
    """A context whose compact UTF-8 serialization is exactly `octets` bytes."""
    free = octets - context_size(BASE_CONTEXT)
    width = len(filler.encode("utf-8"))
    pad = filler * (free // width) + "a" * (free % width)
    context = {**BASE_CONTEXT, "pad": pad}
    assert context_size(context) == octets
    return context


def test_a_valid_event_decodes_into_the_domain_event():
    body = escalation_body()

    event = decode_escalation_requested(json.dumps(body))

    assert str(event.event_id) == body["event_id"]
    assert str(event.escalation_id) == body["escalation_id"]
    assert (str(event.user_id), str(event.journal_id), str(event.update_id)) == (
        body["user_id"],
        body["journal_id"],
        body["update_id"],
    )
    assert event.occurred_at == NOW - HOUR
    assert event.accept_until.tzinfo is not None
    assert event.context == body["context"]


def test_unknown_fields_are_ignored():
    event = decode_escalation_requested(encode(added_later="x"))

    assert event.context["schema_version"] == 1


@pytest.mark.parametrize(
    "field",
    [
        "event_id",
        "occurred_at",
        "escalation_id",
        "user_id",
        "journal_id",
        "update_id",
        "accept_until",
        "context",
    ],
)
def test_every_documented_field_is_required(field: str):
    body = escalation_body()
    del body[field]

    with pytest.raises(InvalidMessage):
        decode_escalation_requested(json.dumps(body))


@pytest.mark.parametrize(
    "overrides",
    [
        {"escalation_id": "not-a-uuid"},
        {"accept_until": "2026-11-12T12:00:00"},
        {"occurred_at": "yesterday"},
        {"context": []},
        {"context": "text"},
    ],
    ids=["bad id", "accept_until without a zone", "bad time", "array context", "string context"],
)
def test_a_malformed_event_is_invalid(overrides: dict):
    with pytest.raises(InvalidMessage):
        decode_escalation_requested(encode(**overrides))


def test_a_body_that_is_not_json_is_invalid():
    with pytest.raises(InvalidMessage):
        decode_escalation_requested("{not json")


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"species": "Basil"},
        {"schema_version": 2},
        {"schema_version": "1"},
        {"schema_version": 1.0},
        {"schema_version": True},
        {"schema_version": None},
    ],
    ids=["empty", "no version", "newer version", "string", "float", "boolean", "null"],
)
def test_the_context_needs_a_supported_schema_version(context: dict):
    with pytest.raises(InvalidMessage, match="schema_version"):
        decode_escalation_requested(encode(context=context))


def test_a_context_exactly_at_the_limit_is_accepted():
    context = context_of_size(CONTEXT_LIMIT_BYTES)

    assert decode_escalation_requested(encode(context=context)).context == context


def test_a_context_one_byte_over_the_limit_is_rejected():
    with pytest.raises(InvalidMessage, match="the limit is 65536"):
        decode_escalation_requested(encode(context=context_of_size(CONTEXT_LIMIT_BYTES + 1)))


def test_the_limit_counts_bytes_not_characters():
    exact = context_of_size(CONTEXT_LIMIT_BYTES, filler="я")
    over = context_of_size(CONTEXT_LIMIT_BYTES + 1, filler="я")

    assert len(json.dumps(exact, ensure_ascii=False)) < CONTEXT_LIMIT_BYTES
    assert decode_escalation_requested(encode(context=exact)).context == exact
    with pytest.raises(InvalidMessage):
        decode_escalation_requested(encode(context=over))


def test_four_byte_characters_count_as_four():
    emoji = context_of_size(CONTEXT_LIMIT_BYTES, filler="\U0001f331")

    assert decode_escalation_requested(encode(context=emoji)).context == emoji
    with pytest.raises(InvalidMessage):
        decode_escalation_requested(
            encode(context=context_of_size(CONTEXT_LIMIT_BYTES + 1, filler="\U0001f331"))
        )


def test_the_limit_is_measured_on_the_compact_form_whatever_the_sender_spaces_add():
    context = context_of_size(CONTEXT_LIMIT_BYTES)
    spaced = json.dumps(escalation_body(context=context), indent=2)

    assert len(json.dumps(context)) > CONTEXT_LIMIT_BYTES
    assert decode_escalation_requested(spaced).context == context


def test_a_lone_surrogate_in_the_context_is_invalid_rather_than_a_crash():
    body = encode().replace('"Why spots?"', '"\\ud800"')

    with pytest.raises(InvalidMessage):
        decode_escalation_requested(body)


def test_the_route_hands_the_decoded_event_to_the_service():
    received = []

    class Service:
        def create_from_escalation(self, event) -> None:
            received.append(event)

    routes = escalation_routes(Service())
    body = escalation_body()

    routes["escalation.requested"](json.dumps(body))

    assert set(routes) == {"escalation.requested"}
    assert [str(event.escalation_id) for event in received] == [body["escalation_id"]]
    assert isinstance(received[0].escalation_id, uuid.UUID)
