import json
import logging
import uuid
from contextvars import copy_context

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from growpath_observability import set_correlation_id

from app.messaging.sns import SnsTicketEvents, create_sns_client, encode_ticket_event
from app.service.events import created_event, terminal_event
from app.service.model import Answer
from app.service.ticket_errors import PublishFailed
from tests.unit.fakes import answered_ticket, cancelled_ticket, make_ticket
from tests.unit.settings import TICKET_TOPIC_ARN, make_settings


class FakeSns:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.error = error

    def publish(self, **kwargs) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)


def test_the_event_goes_to_the_topic_with_its_type_and_the_correlation_id():
    client = FakeSns()
    event = created_event(make_ticket())

    def publish_under_a_request_id() -> None:
        set_correlation_id("req-5")
        SnsTicketEvents(client, TICKET_TOPIC_ARN).publish(event)

    copy_context().run(publish_under_a_request_id)

    (call,) = client.calls
    assert call["TopicArn"] == TICKET_TOPIC_ARN
    assert call["MessageAttributes"] == {
        "event_type": {"DataType": "String", "StringValue": "ticket.created"},
        "correlation_id": {"DataType": "String", "StringValue": "req-5"},
    }


@pytest.mark.parametrize(
    ("event", "event_type"),
    [
        (created_event(make_ticket()), "ticket.created"),
        (terminal_event(answered_ticket()), "ticket.answered"),
        (terminal_event(cancelled_ticket()), "ticket.cancelled"),
    ],
)
def test_the_event_type_attribute_follows_the_event(event, event_type: str):
    client = FakeSns()

    SnsTicketEvents(client, TICKET_TOPIC_ARN).publish(event)

    attribute = client.calls[0]["MessageAttributes"]["event_type"]
    assert attribute["StringValue"] == event_type


def test_the_message_matches_the_shared_contract_and_stays_utf8():
    ticket = answered_ticket(answer=Answer(uuid.uuid4(), "Поливайте реже"))
    client = FakeSns()

    SnsTicketEvents(client, TICKET_TOPIC_ARN).publish(terminal_event(ticket))

    message = client.calls[0]["Message"]
    body = json.loads(message)
    assert "Поливайте реже" in message
    assert body["event_id"] == str(terminal_event(ticket).event_id)
    assert body["ticket_id"] == str(ticket.id)
    assert body["occurred_at"] == ticket.answered_at.isoformat()
    assert body["answered_at"] == ticket.answered_at.isoformat()
    assert body["answer_text"] == "Поливайте реже"
    assert ", " not in message and ": " not in message


def test_the_same_event_serializes_to_the_same_message():
    event = terminal_event(cancelled_ticket())

    assert encode_ticket_event(event) == encode_ticket_event(event)


def test_a_published_event_is_logged_with_its_id(caplog: pytest.LogCaptureFixture):
    event = created_event(make_ticket())

    with caplog.at_level(logging.INFO, logger="app.messaging.sns"):
        SnsTicketEvents(FakeSns(), TICKET_TOPIC_ARN).publish(event)

    (record,) = caplog.records
    assert (record.event_type, record.event_id) == ("ticket.created", str(event.event_id))


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "Publish"),
        EndpointConnectionError(endpoint_url="http://sns"),
    ],
    ids=["rejected by SNS", "SNS unreachable"],
)
def test_a_broker_failure_becomes_publish_failed(error: Exception):
    with pytest.raises(PublishFailed, match="ticket.created"):
        SnsTicketEvents(FakeSns(error), TICKET_TOPIC_ARN).publish(created_event(make_ticket()))


def test_the_client_uses_the_configured_timeout_and_endpoint():
    client = create_sns_client(
        make_settings(aws_read_timeout_seconds=7, aws_endpoint_url="http://s:1")
    )

    assert client.meta.config.read_timeout == 7
    assert client.meta.endpoint_url == "http://s:1"
