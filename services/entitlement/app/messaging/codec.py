import uuid

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from app.service.consumer import Route
from app.service.errors import InvalidMessage
from app.service.event_handlers import EventHandlers
from app.service.events import OrderPlaced, TicketCancelled, TicketCreated

# `ticket.answered` reaches the queue when the subscription has no filter; it carries nothing
# for this service because the expert quota is committed at `ticket.created`.
TICKET_EVENTS_IGNORED = frozenset({"ticket.answered"})


class OrderPlacedBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_id: uuid.UUID
    user_id: uuid.UUID
    total_paid_cents: int
    placed_at: AwareDatetime


class TicketBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    escalation_id: uuid.UUID
    user_id: uuid.UUID


def decode_order_placed(body: str) -> OrderPlaced:
    parsed = _parse(OrderPlacedBody, body)
    return OrderPlaced(parsed.order_id, parsed.user_id, parsed.total_paid_cents, parsed.placed_at)


def decode_ticket_created(body: str) -> TicketCreated:
    parsed = _parse(TicketBody, body)
    return TicketCreated(parsed.escalation_id, parsed.user_id)


def decode_ticket_cancelled(body: str) -> TicketCancelled:
    parsed = _parse(TicketBody, body)
    return TicketCancelled(parsed.escalation_id, parsed.user_id)


def order_routes(handlers: EventHandlers) -> dict[str, Route]:
    return {"order.placed": lambda body: handlers.order_placed(decode_order_placed(body))}


def ticket_routes(handlers: EventHandlers) -> dict[str, Route]:
    return {
        "ticket.created": lambda body: handlers.ticket_created(decode_ticket_created(body)),
        "ticket.cancelled": lambda body: handlers.ticket_cancelled(decode_ticket_cancelled(body)),
    }


def _parse[Body: BaseModel](model: type[Body], body: str) -> Body:
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        raise InvalidMessage(f"the message does not match the event contract: {error}") from error
