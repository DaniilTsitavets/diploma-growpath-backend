import json
import uuid
from dataclasses import asdict
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from growpath_observability import get_correlation_id

from app.config import Settings
from app.service.errors import PublishFailed
from app.service.escalation_events import EscalationRequested


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


def encode_escalation_requested(event: EscalationRequested) -> str:
    return json.dumps(
        asdict(event), default=_json_default, ensure_ascii=False, separators=(",", ":")
    )


class SnsEscalationEvents:
    def __init__(self, client, topic_arn: str) -> None:
        self._client = client
        self._topic_arn = topic_arn

    def publish_escalation_requested(self, event: EscalationRequested) -> None:
        attributes = {"event_type": _string_attribute("escalation.requested")}
        if correlation_id := get_correlation_id():
            attributes["correlation_id"] = _string_attribute(correlation_id)
        try:
            self._client.publish(
                TopicArn=self._topic_arn,
                Message=encode_escalation_requested(event),
                MessageAttributes=attributes,
            )
        except (BotoCoreError, ClientError) as error:
            raise PublishFailed("escalation.requested was rejected by SNS") from error
