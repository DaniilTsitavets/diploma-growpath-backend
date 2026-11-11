import uuid
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from app.service.consumer import InvalidMessage, Route
from app.service.ticket_events import TicketAnswered, TicketCancelled, TicketCreated
from app.service.ticket_handlers import TicketEventHandlers

ANSWER_LIMIT = 5000


class TicketBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: uuid.UUID
    escalation_id: uuid.UUID


class TicketAnsweredBody(TicketBody):
    answer_text: Annotated[str, Field(min_length=1, max_length=ANSWER_LIMIT)]
    answered_at: AwareDatetime


def decode_ticket_created(body: str) -> TicketCreated:
    parsed = _parse(TicketBody, body)
    return TicketCreated(parsed.escalation_id, parsed.ticket_id)


def decode_ticket_answered(body: str) -> TicketAnswered:
    parsed = _parse(TicketAnsweredBody, body)
    return TicketAnswered(
        parsed.escalation_id, parsed.ticket_id, parsed.answer_text, parsed.answered_at
    )


def decode_ticket_cancelled(body: str) -> TicketCancelled:
    parsed = _parse(TicketBody, body)
    return TicketCancelled(parsed.escalation_id, parsed.ticket_id)


def ticket_routes(handlers: TicketEventHandlers) -> dict[str, Route]:
    return {
        "ticket.created": lambda body: handlers.ticket_created(decode_ticket_created(body)),
        "ticket.answered": lambda body: handlers.ticket_answered(decode_ticket_answered(body)),
        "ticket.cancelled": lambda body: handlers.ticket_cancelled(decode_ticket_cancelled(body)),
    }


def _parse[Body: BaseModel](model: type[Body], body: str) -> Body:
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        raise InvalidMessage(f"the message does not match the event contract: {error}") from error
