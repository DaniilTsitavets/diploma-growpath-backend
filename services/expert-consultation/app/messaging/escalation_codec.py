import json
import uuid
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, field_validator

from app.service.events import EscalationRequested
from app.service.queue import InvalidMessage, Route
from app.service.tickets import TicketService

CONTEXT_LIMIT_BYTES = 64 * 1024
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


def context_size(context: dict[str, Any]) -> int:
    """UTF-8 bytes of the compact serialization, which is how plant-care measures it."""
    return len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class EscalationRequestedBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: uuid.UUID
    occurred_at: AwareDatetime
    escalation_id: uuid.UUID
    user_id: uuid.UUID
    journal_id: uuid.UUID
    update_id: uuid.UUID
    accept_until: AwareDatetime
    context: dict[str, Any]

    @field_validator("context")
    @classmethod
    def _require_supported_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        version = context.get("schema_version")
        if type(version) is not int or version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported context schema_version: {version!r}")
        if (size := context_size(context)) > CONTEXT_LIMIT_BYTES:
            raise ValueError(f"the context is {size} bytes, the limit is {CONTEXT_LIMIT_BYTES}")
        return context


def decode_escalation_requested(body: str) -> EscalationRequested:
    try:
        parsed = EscalationRequestedBody.model_validate_json(body)
    except ValidationError as error:
        raise InvalidMessage(f"the message does not match the event contract: {error}") from error
    return EscalationRequested(**parsed.model_dump())


def escalation_routes(service: TicketService) -> dict[str, Route]:
    return {
        "escalation.requested": lambda body: service.create_from_escalation(
            decode_escalation_requested(body)
        )
    }
