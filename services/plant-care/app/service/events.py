import uuid
from dataclasses import dataclass
from enum import StrEnum


class ItemType(StrEnum):
    PACK = "pack"
    SENSOR = "sensor"


@dataclass(frozen=True)
class OrderPlaced:
    order_id: uuid.UUID
    user_id: uuid.UUID
    item_type: ItemType
    plant_species: str | None
