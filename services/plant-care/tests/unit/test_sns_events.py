import json
import uuid

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from growpath_observability import set_correlation_id

from app.messaging.sns import SnsEscalationEvents, create_sns_client
from app.service.errors import PublishFailed
from app.service.escalation_events import escalation_requested
from tests.unit.delivery_scenario import ACK_MARGIN, TTL, DeliveryScenario
from tests.unit.settings import ESCALATION_TOPIC_ARN, make_settings


class FakeSns:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.error = error

    def publish(self, **kwargs) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)


def event():
    scenario = DeliveryScenario()
    escalation = scenario.escalate()
    context = {"schema_version": 1, "user_question": "Почему пятна?"}
    return escalation, escalation_requested(escalation, context, TTL, ACK_MARGIN)


def test_the_event_is_published_to_the_topic_with_its_type_and_correlation_id():
    set_correlation_id("req-5")
    client = FakeSns()
    _, requested = event()

    SnsEscalationEvents(client, ESCALATION_TOPIC_ARN).publish_escalation_requested(requested)

    (call,) = client.calls
    assert call["TopicArn"] == ESCALATION_TOPIC_ARN
    assert call["MessageAttributes"] == {
        "event_type": {"DataType": "String", "StringValue": "escalation.requested"},
        "correlation_id": {"DataType": "String", "StringValue": "req-5"},
    }


def test_the_message_matches_the_contract_and_stays_utf8():
    client = FakeSns()
    escalation, requested = event()

    SnsEscalationEvents(client, ESCALATION_TOPIC_ARN).publish_escalation_requested(requested)

    message = client.calls[0]["Message"]
    body = json.loads(message)
    assert "Почему пятна?" in message
    assert set(body) == {
        "event_id",
        "occurred_at",
        "escalation_id",
        "user_id",
        "journal_id",
        "update_id",
        "accept_until",
        "context",
    }
    assert body["event_id"] == body["escalation_id"] == str(escalation.id)
    assert body["occurred_at"] == escalation.created_at.isoformat()
    assert body["accept_until"] == (escalation.created_at + TTL - ACK_MARGIN).isoformat()
    assert body["context"] == {"schema_version": 1, "user_question": "Почему пятна?"}
    assert ", " not in message and ": " not in message


def test_the_same_event_serializes_to_the_same_message():
    first, second = FakeSns(), FakeSns()
    _, requested = event()

    SnsEscalationEvents(first, "arn").publish_escalation_requested(requested)
    SnsEscalationEvents(second, "arn").publish_escalation_requested(requested)

    assert first.calls[0]["Message"] == second.calls[0]["Message"]


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "Publish"),
        EndpointConnectionError(endpoint_url="http://sns"),
    ],
)
def test_a_broker_failure_becomes_publish_failed(error: Exception):
    _, requested = event()

    with pytest.raises(PublishFailed):
        SnsEscalationEvents(FakeSns(error), "arn").publish_escalation_requested(requested)


def test_the_client_uses_the_configured_timeout_and_endpoint():
    client = create_sns_client(
        make_settings(aws_read_timeout_seconds=7, aws_endpoint_url="http://s:1")
    )

    assert client.meta.config.read_timeout == 7
    assert client.meta.endpoint_url == "http://s:1"


def test_the_event_id_differs_per_escalation():
    assert event()[1].event_id != event()[1].event_id
    assert isinstance(event()[1].event_id, uuid.UUID)
