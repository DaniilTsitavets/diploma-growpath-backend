import json
from contextvars import copy_context
from datetime import timedelta

import pytest
from growpath_observability import set_correlation_id

from app.messaging.codec import decode_order_placed
from app.messaging.sns import SnsEscalationEvents, encode_escalation_requested
from app.messaging.ticket_codec import (
    decode_ticket_answered,
    decode_ticket_cancelled,
    decode_ticket_created,
)
from app.service.consumer import InvalidMessage
from app.service.model import DiagnosisStatus, HistoryEntry
from tests.unit.delivery_scenario import DeliveryScenario
from tests.unit.event_contracts import (
    attribute_errors,
    example_payloads,
    payload_errors,
    required_fields,
)
from tests.unit.fakes import NOW
from tests.unit.settings import ESCALATION_TOPIC_ARN

ORDER_FIELDS_READ = {"order_id", "user_id", "item_type"}
TICKET_FIELDS_READ = {"ticket_id", "escalation_id"}
# message -> (codec, the fields the codec needs); the rest of the schema is carried but not read.
# plant_species is optional for the codec: the handler rejects a pack without it.
CONSUMED = {
    "OrderPlaced": (decode_order_placed, ORDER_FIELDS_READ),
    "TicketCreated": (decode_ticket_created, TICKET_FIELDS_READ),
    "TicketAnswered": (
        decode_ticket_answered,
        TICKET_FIELDS_READ | {"answer_text", "answered_at"},
    ),
    "TicketCancelled": (decode_ticket_cancelled, TICKET_FIELDS_READ),
}
EXAMPLES = [(message, name) for message in CONSUMED for name in example_payloads(message)]
ESCALATION = "EscalationRequested"


class CapturingSns:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def publish(self, **kwargs) -> None:
        self.calls.append(kwargs)


def accepted(codec, payload: dict) -> bool:
    try:
        codec(json.dumps(payload))
    except InvalidMessage:
        return False
    return True


def published_escalations() -> dict[str, str]:
    """Real events built by the publisher from stored rows, as the broker receives them."""
    plain = DeliveryScenario()
    plain.publisher.publish(plain.escalate())
    asked = DeliveryScenario()
    asked.publisher.publish(asked.escalate(user_question="Why the spots?"))
    later = DeliveryScenario()
    later.uow.escalation_delivery.history_entries = [
        HistoryEntry(NOW - timedelta(days=2), DiagnosisStatus.COMPLETED, "Overwatering"),
        HistoryEntry(NOW - timedelta(days=1), None, None),
    ]
    later.publisher.publish(later.escalate())
    return {
        name: encode_escalation_requested(scenario.events.published[-1])
        for name, scenario in {"plain": plain, "asked": asked, "later": later}.items()
    }


PUBLISHED = published_escalations()


@pytest.mark.parametrize("name", PUBLISHED)
def test_the_published_escalation_satisfies_the_contract(name: str):
    assert payload_errors(ESCALATION, json.loads(PUBLISHED[name])) == []


def test_the_published_attributes_satisfy_the_contract():
    sns = CapturingSns()
    event = DeliveryScenario()
    event.publisher.publish(event.escalate())

    def publish_under_a_request_id() -> None:
        set_correlation_id("req-5")
        SnsEscalationEvents(sns, ESCALATION_TOPIC_ARN).publish_escalation_requested(
            event.events.published[0]
        )

    copy_context().run(publish_under_a_request_id)

    (call,) = sns.calls
    assert attribute_errors(ESCALATION, call["MessageAttributes"]) == []
    assert payload_errors(ESCALATION, json.loads(call["Message"])) == []


@pytest.mark.parametrize("name", example_payloads(ESCALATION))
def test_the_escalation_examples_satisfy_the_contract(name: str):
    assert payload_errors(ESCALATION, example_payloads(ESCALATION)[name]) == []


def test_the_contract_rejects_a_context_of_another_version():
    payload = example_payloads(ESCALATION)["after a diagnosis"]
    payload["context"]["schema_version"] = 2

    assert payload_errors(ESCALATION, payload)


@pytest.mark.parametrize(("message", "name"), EXAMPLES)
def test_the_consumed_examples_satisfy_the_contract(message: str, name: str):
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


@pytest.mark.parametrize("answer", ["", "x" * 5001], ids=["empty", "too long"])
def test_the_codec_and_the_contract_both_reject_a_bad_answer_text(answer: str):
    payload = {**example_payloads("TicketAnswered")["answered"], "answer_text": answer}

    assert payload_errors("TicketAnswered", payload)
    assert not accepted(decode_ticket_answered, payload)


@pytest.mark.parametrize("message", ["TicketCreated", "TicketCancelled", "TicketAnswered"])
def test_the_codec_ignores_fields_the_contract_may_add(message: str):
    codec, _ = CONSUMED[message]
    payload = {**next(iter(example_payloads(message).values())), "added_later": 1}

    assert accepted(codec, payload)
