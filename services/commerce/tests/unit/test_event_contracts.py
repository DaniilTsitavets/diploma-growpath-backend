import json
from contextvars import copy_context

import pytest
from growpath_observability import set_correlation_id

from app.messaging.sns import SnsOrderEvents
from app.service.events import order_placed
from tests.unit.event_contracts import attribute_errors, example_payloads, payload_errors
from tests.unit.fakes import TOPIC_ARN, make_item, make_order_data

MESSAGE = "OrderPlaced"


class CapturingSns:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def publish(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"MessageId": "m-1"}


def publish(item, **order) -> dict:
    sns = CapturingSns()
    event = order_placed(make_order_data(item_id=item.id, **order), item)
    SnsOrderEvents(sns, TOPIC_ARN).publish_order_placed(event)
    (call,) = sns.calls
    return call


PUBLISHED = {
    "a pack with the discount": (make_item(), {"discount_cents": 500, "total_paid_cents": 2000}),
    "a pack at full price": (make_item(), {}),
    "a sensor": (make_item(item_type="sensor", plant_species=None, seed_count=None), {}),
}


@pytest.mark.parametrize("case", PUBLISHED)
def test_the_published_payload_satisfies_the_contract(case: str):
    item, order = PUBLISHED[case]

    call = publish(item, **order)

    assert payload_errors(MESSAGE, json.loads(call["Message"])) == []


def test_the_published_attributes_satisfy_the_contract_with_a_correlation_id():
    def publish_under_a_request_id() -> dict:
        set_correlation_id("req-5")
        return publish(make_item())

    call = copy_context().run(publish_under_a_request_id)

    assert call["MessageAttributes"]["correlation_id"]["StringValue"] == "req-5"
    assert attribute_errors(MESSAGE, call["MessageAttributes"]) == []


def test_the_published_attributes_satisfy_the_contract_without_a_correlation_id():
    call = copy_context().run(publish, make_item())

    assert attribute_errors(MESSAGE, call["MessageAttributes"]) == []


@pytest.mark.parametrize("name", example_payloads(MESSAGE))
def test_the_contract_examples_satisfy_the_contract(name: str):
    assert payload_errors(MESSAGE, example_payloads(MESSAGE)[name]) == []


def test_the_contract_rejects_a_pack_without_a_species():
    payload = example_payloads(MESSAGE)["pack"]
    payload["plant_species"] = None

    assert payload_errors(MESSAGE, payload)


def test_the_contract_rejects_a_payload_missing_a_required_field():
    payload = example_payloads(MESSAGE)["pack"]
    del payload["total_paid_cents"]

    assert payload_errors(MESSAGE, payload)
