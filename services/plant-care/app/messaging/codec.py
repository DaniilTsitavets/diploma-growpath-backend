import uuid

from pydantic import BaseModel, ConfigDict, ValidationError

from app.service.consumer import InvalidMessage, Route
from app.service.event_handlers import EventHandlers
from app.service.events import ItemType, OrderPlaced


class OrderPlacedBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_id: uuid.UUID
    user_id: uuid.UUID
    item_type: ItemType
    plant_species: str | None = None


def decode_order_placed(body: str) -> OrderPlaced:
    try:
        parsed = OrderPlacedBody.model_validate_json(body)
    except ValidationError as error:
        raise InvalidMessage(f"the message does not match the event contract: {error}") from error
    return OrderPlaced(parsed.order_id, parsed.user_id, parsed.item_type, parsed.plant_species)


def order_routes(handlers: EventHandlers) -> dict[str, Route]:
    return {"order.placed": lambda body: handlers.order_placed(decode_order_placed(body))}
