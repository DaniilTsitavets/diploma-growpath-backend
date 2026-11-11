import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

from app.messaging.sqs import SqsQueue, create_sqs_client
from app.service.queue import QueueMessage, QueueUnavailable
from tests.unit.settings import ESCALATION_QUEUE_URL, make_settings

BROKER_ERRORS = [
    ClientError({"Error": {"Code": "InternalError", "Message": "boom"}}, "ReceiveMessage"),
    EndpointConnectionError(endpoint_url="http://localstack:4566"),
]
BROKER_IDS = ["rejected by SQS", "SQS unreachable"]


class FailingSqs:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def receive_message(self, **kwargs):
        raise self._error

    delete_message = get_queue_attributes = receive_message


@pytest.fixture
def client():
    return boto3.client(
        "sqs", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )


def test_received_message_exposes_body_handle_and_string_attributes(client):
    with Stubber(client) as stubber:
        stubber.add_response(
            "receive_message",
            {
                "Messages": [
                    {
                        "MessageId": "m-1",
                        "ReceiptHandle": "r-1",
                        "Body": '{"escalation_id": "1"}',
                        "MessageAttributes": {
                            "event_type": {
                                "DataType": "String",
                                "StringValue": "escalation.requested",
                            },
                            "correlation_id": {"DataType": "String", "StringValue": "req-1"},
                            "blob": {"DataType": "Binary", "BinaryValue": b"x"},
                        },
                    }
                ]
            },
            {
                "QueueUrl": ESCALATION_QUEUE_URL,
                "MaxNumberOfMessages": 5,
                "WaitTimeSeconds": 3,
                "MessageAttributeNames": ["All"],
            },
        )

        (message,) = SqsQueue(client, ESCALATION_QUEUE_URL).receive(5, 3)

    assert message == QueueMessage(
        "m-1",
        "r-1",
        '{"escalation_id": "1"}',
        {"event_type": "escalation.requested", "correlation_id": "req-1"},
    )


def test_an_empty_poll_returns_no_messages(client):
    with Stubber(client) as stubber:
        stubber.add_response("receive_message", {})

        assert SqsQueue(client, ESCALATION_QUEUE_URL).receive(5, 0) == []


def test_delete_uses_the_receipt_handle(client):
    with Stubber(client) as stubber:
        stubber.add_response(
            "delete_message", {}, {"QueueUrl": ESCALATION_QUEUE_URL, "ReceiptHandle": "r-1"}
        )

        SqsQueue(client, ESCALATION_QUEUE_URL).delete(QueueMessage("m-1", "r-1", "{}", {}))

        stubber.assert_no_pending_responses()


def test_check_asks_for_the_queue_attributes(client):
    with Stubber(client) as stubber:
        stubber.add_response(
            "get_queue_attributes",
            {"Attributes": {"QueueArn": "arn:aws:sqs:us-east-1:000000000000:q"}},
            {"QueueUrl": ESCALATION_QUEUE_URL, "AttributeNames": ["QueueArn"]},
        )

        SqsQueue(client, ESCALATION_QUEUE_URL).check()

        stubber.assert_no_pending_responses()


@pytest.mark.parametrize("error", BROKER_ERRORS, ids=BROKER_IDS)
@pytest.mark.parametrize("operation", ["receive", "delete", "check"])
def test_broker_errors_become_queue_unavailable(error: Exception, operation: str):
    queue = SqsQueue(FailingSqs(error), ESCALATION_QUEUE_URL)
    calls = {
        "receive": lambda: queue.receive(1, 0),
        "delete": lambda: queue.delete(QueueMessage("m", "r", "{}", {})),
        "check": queue.check,
    }

    with pytest.raises(QueueUnavailable):
        calls[operation]()


def test_client_uses_the_configured_endpoint_region_and_a_read_timeout_above_the_poll():
    settings = make_settings(
        aws_endpoint_url="http://localstack:4566", aws_region="eu-west-1", consumer_wait_seconds=20
    )

    client = create_sqs_client(settings)

    assert client.meta.endpoint_url == "http://localstack:4566"
    assert client.meta.region_name == "eu-west-1"
    assert client.meta.config.read_timeout > 20
