import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.service.queue import QueueMessage, QueueUnavailable

READ_TIMEOUT_MARGIN_SECONDS = 10


def create_sqs_client(settings: Settings):
    return boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        # Long polling holds the connection for the whole wait, so the read timeout must exceed it.
        # The standard retry mode backs off exponentially with jitter.
        config=Config(
            connect_timeout=settings.aws_connect_timeout_seconds,
            read_timeout=settings.consumer_wait_seconds + READ_TIMEOUT_MARGIN_SECONDS,
            retries={"max_attempts": settings.aws_max_attempts, "mode": "standard"},
        ),
    )


class SqsQueue:
    def __init__(self, client, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def receive(self, max_messages: int, wait_seconds: int) -> list[QueueMessage]:
        try:
            response = self._client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_seconds,
                MessageAttributeNames=["All"],
            )
        except (BotoCoreError, ClientError) as error:
            raise QueueUnavailable("could not receive from the queue") from error
        return [_to_message(raw) for raw in response.get("Messages", [])]

    def delete(self, message: QueueMessage) -> None:
        try:
            self._client.delete_message(
                QueueUrl=self._queue_url, ReceiptHandle=message.receipt_handle
            )
        except (BotoCoreError, ClientError) as error:
            raise QueueUnavailable("could not delete from the queue") from error

    def check(self) -> None:
        try:
            self._client.get_queue_attributes(QueueUrl=self._queue_url, AttributeNames=["QueueArn"])
        except (BotoCoreError, ClientError) as error:
            raise QueueUnavailable("the queue is not reachable") from error


def _to_message(raw: dict) -> QueueMessage:
    attributes = {
        name: attribute["StringValue"]
        for name, attribute in raw.get("MessageAttributes", {}).items()
        if "StringValue" in attribute
    }
    return QueueMessage(raw["MessageId"], raw["ReceiptHandle"], raw["Body"], attributes)
