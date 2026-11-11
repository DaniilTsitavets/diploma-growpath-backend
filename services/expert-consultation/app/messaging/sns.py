import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from growpath_observability import get_correlation_id

from app.config import Settings
from app.service.events import TicketEvent
from app.service.ticket_errors import PublishFailed

logger = logging.getLogger(__name__)


def create_sns_client(settings: Settings):
    return boto3.client(
        "sns",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        # The standard retry mode backs off exponentially with jitter.
        config=Config(
            connect_timeout=settings.aws_connect_timeout_seconds,
            read_timeout=settings.aws_read_timeout_seconds,
            retries={"max_attempts": settings.aws_max_attempts, "mode": "standard"},
        ),
    )


def _json_default(value: object) -> str:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not part of the event contract")


def _string_attribute(value: str) -> dict[str, str]:
    return {"DataType": "String", "StringValue": value}


def encode_ticket_event(event: TicketEvent) -> str:
    return json.dumps(
        asdict(event), default=_json_default, ensure_ascii=False, separators=(",", ":")
    )


class SnsTicketEvents:
    def __init__(self, client, topic_arn: str) -> None:
        self._client = client
        self._topic_arn = topic_arn

    def publish(self, event: TicketEvent) -> None:
        attributes = {"event_type": _string_attribute(event.event_type)}
        if correlation_id := get_correlation_id():
            attributes["correlation_id"] = _string_attribute(correlation_id)
        try:
            self._client.publish(
                TopicArn=self._topic_arn,
                Message=encode_ticket_event(event),
                MessageAttributes=attributes,
            )
        except (BotoCoreError, ClientError) as error:
            raise PublishFailed(f"{event.event_type} was rejected by SNS") from error
        logger.info(
            "event published",
            extra={"event_type": event.event_type, "event_id": str(event.event_id)},
        )
