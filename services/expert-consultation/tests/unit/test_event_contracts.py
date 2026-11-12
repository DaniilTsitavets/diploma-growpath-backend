import json
from contextvars import copy_context

import pytest
from growpath_observability import set_correlation_id

from app.messaging.escalation_codec import CONTEXT_LIMIT_BYTES, decode_escalation_requested
from app.messaging.sns import SnsTicketEvents, encode_ticket_event
from app.service.events import created_event, terminal_event
from app.service.queue import InvalidMessage
from tests.unit.event_contracts import (
    attribute_errors,
    example_payloads,
    payload_errors,
    required_fields,
)
from tests.unit.fakes import answered_ticket, cancelled_ticket, make_ticket
from tests.unit.settings import TICKET_TOPIC_ARN

ESCALATION = "EscalationRequested"
PUBLISHED = {
    "TicketCreated": lambda: created_event(make_ticket()),
    "TicketAnswered": lambda: terminal_event(answered_ticket()),
    "TicketCancelled": lambda: terminal_event(cancelled_ticket()),
}


class CapturingSns:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def publish(self, **kwargs) -> None:
        self.calls.append(kwargs)


def accepted(payload: dict) -> bool:
    try:
        decode_escalation_requested(json.dumps(payload))
    except InvalidMessage:
        return False
    return True


@pytest.mark.parametrize("message", PUBLISHED)
def test_the_published_ticket_event_satisfies_the_contract(message: str):
    body = encode_ticket_event(PUBLISHED[message]())

    assert payload_errors(message, json.loads(body)) == []


@pytest.mark.parametrize("message", PUBLISHED)
def test_the_published_attributes_satisfy_the_contract(message: str):
    sns = CapturingSns()

    def publish_under_a_request_id() -> None:
        set_correlation_id("req-5")
        SnsTicketEvents(sns, TICKET_TOPIC_ARN).publish(PUBLISHED[message]())

    copy_context().run(publish_under_a_request_id)

    (call,) = sns.calls
    assert attribute_errors(message, call["MessageAttributes"]) == []
    assert payload_errors(message, json.loads(call["Message"])) == []


@pytest.mark.parametrize("message", PUBLISHED)
def test_the_ticket_examples_satisfy_the_contract(message: str):
    for payload in example_payloads(message).values():
        assert payload_errors(message, payload) == []


@pytest.mark.parametrize("name", example_payloads(ESCALATION))
def test_the_escalation_examples_satisfy_the_contract(name: str):
    assert payload_errors(ESCALATION, example_payloads(ESCALATION)[name]) == []


@pytest.mark.parametrize("name", example_payloads(ESCALATION))
def test_the_codec_accepts_the_contract_examples(name: str):
    assert accepted(example_payloads(ESCALATION)[name])


def test_the_codec_rejects_every_payload_that_lacks_a_required_field():
    example = example_payloads(ESCALATION)["after a diagnosis"]

    for field in required_fields(ESCALATION):
        broken = {key: value for key, value in example.items() if key != field}
        assert payload_errors(ESCALATION, broken), f"the contract lets {field} be missing"
        assert not accepted(broken), f"the codec accepts a payload without {field}"


@pytest.mark.parametrize("version", [2, "1", None], ids=repr)
def test_a_context_of_another_version_breaks_the_contract_and_the_codec(version: object):
    payload = example_payloads(ESCALATION)["after a diagnosis"]
    payload["context"]["schema_version"] = version

    assert payload_errors(ESCALATION, payload)
    assert not accepted(payload)


def test_a_malformed_user_id_breaks_the_contract_and_the_codec():
    payload = {**example_payloads(ESCALATION)["after a diagnosis"], "user_id": "not-a-uuid"}

    assert payload_errors(ESCALATION, payload)
    assert not accepted(payload)


def test_the_codec_enforces_the_size_limit_the_contract_only_describes():
    payload = example_payloads(ESCALATION)["after a diagnosis"]
    payload["context"]["user_question"] = "x" * CONTEXT_LIMIT_BYTES

    assert payload_errors(ESCALATION, payload) == []
    assert not accepted(payload)


def test_the_codec_reads_only_the_version_of_the_context():
    payload = example_payloads(ESCALATION)["after a diagnosis"]
    payload["context"] = {"schema_version": 1, "added_later": True}

    assert payload_errors(ESCALATION, payload)
    assert accepted(payload)
