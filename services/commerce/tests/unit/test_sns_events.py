import json
from contextvars import Context, copy_context

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import ANY, Stubber
from growpath_observability import set_correlation_id

from app.messaging.sns import SnsOrderEvents, create_sns_client
from app.service.errors import PublishFailed
from app.service.events import order_placed
from tests.unit.fakes import TOPIC_ARN, make_item, make_order_data, make_settings

CONTRACT_FIELDS = {
    "event_id",
    "occurred_at",
    "order_id",
    "user_id",
    "item_id",
    "item_type",
    "plant_species",
    "seed_count",
    "total_paid_cents",
    "discount_applied",
    "placed_at",
}


class FakeSns:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._error = error

    def publish(self, **kwargs) -> dict:
        if self._error:
            raise self._error
        self.calls.append(kwargs)
        return {"MessageId": "m-1"}


def a_placed_event():
    item = make_item()
    return order_placed(make_order_data(item_id=item.id, discount_cents=500), item)


def test_message_body_carries_exactly_the_contract_fields():
    sns, event = FakeSns(), a_placed_event()

    SnsOrderEvents(sns, TOPIC_ARN).publish_order_placed(event)

    (call,) = sns.calls
    body = json.loads(call["Message"])
    assert call["TopicArn"] == TOPIC_ARN
    assert set(body) == CONTRACT_FIELDS
    assert body["event_id"] == body["order_id"] == str(event.order_id)
    assert body["occurred_at"] == body["placed_at"] == event.placed_at.isoformat()
    assert (body["user_id"], body["item_id"]) == (str(event.user_id), str(event.item_id))
    assert body["discount_applied"] is True


def test_attributes_carry_the_event_type_and_the_correlation_id():
    sns = FakeSns()
    publisher = SnsOrderEvents(sns, TOPIC_ARN)

    def publish_in_a_request():
        set_correlation_id("req-42")
        publisher.publish_order_placed(a_placed_event())

    copy_context().run(publish_in_a_request)

    (call,) = sns.calls
    assert call["MessageAttributes"] == {
        "event_type": {"DataType": "String", "StringValue": "order.placed"},
        "correlation_id": {"DataType": "String", "StringValue": "req-42"},
    }


def test_attributes_omit_the_correlation_id_outside_a_request():
    sns = FakeSns()

    Context().run(SnsOrderEvents(sns, TOPIC_ARN).publish_order_placed, a_placed_event())

    assert set(sns.calls[0]["MessageAttributes"]) == {"event_type"}


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "InternalError", "Message": "boom"}}, "Publish"),
        EndpointConnectionError(endpoint_url="http://localstack:4566"),
    ],
    ids=["rejected by SNS", "SNS unreachable"],
)
def test_broker_errors_become_publish_failed(error: Exception):
    publisher = SnsOrderEvents(FakeSns(error), TOPIC_ARN)

    with pytest.raises(PublishFailed):
        publisher.publish_order_placed(a_placed_event())


def test_publish_parameters_are_valid_for_the_sns_api():
    client = boto3.client(
        "sns", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    with Stubber(client) as stubber:
        stubber.add_response(
            "publish",
            {"MessageId": "m-1"},
            {"TopicArn": TOPIC_ARN, "Message": ANY, "MessageAttributes": ANY},
        )

        copy_context().run(SnsOrderEvents(client, TOPIC_ARN).publish_order_placed, a_placed_event())

        stubber.assert_no_pending_responses()


def test_client_uses_the_configured_endpoint_and_region():
    settings = make_settings(aws_endpoint_url="http://localstack:4566", aws_region="eu-west-1")

    client = create_sns_client(settings)

    assert client.meta.endpoint_url == "http://localstack:4566"
    assert client.meta.region_name == "eu-west-1"
