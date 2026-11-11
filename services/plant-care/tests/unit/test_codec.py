import json
import uuid

import pytest

from app.messaging.codec import decode_order_placed
from app.service.consumer import InvalidMessage
from app.service.events import ItemType
from tests.unit.fake_queue import order_placed_body


def test_a_pack_event_carries_the_fields_the_journal_needs():
    body = order_placed_body(plant_species="Mint")

    event = decode_order_placed(json.dumps(body))

    assert event.order_id == uuid.UUID(body["order_id"])
    assert event.user_id == uuid.UUID(body["user_id"])
    assert (event.item_type, event.plant_species) == (ItemType.PACK, "Mint")


def test_a_sensor_event_has_no_species():
    event = decode_order_placed(
        json.dumps(order_placed_body(item_type="sensor", plant_species=None, seed_count=None))
    )

    assert (event.item_type, event.plant_species) == (ItemType.SENSOR, None)


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        json.dumps({"order_id": str(uuid.uuid4())}),
        json.dumps(order_placed_body(item_type="subscription")),
        json.dumps(order_placed_body(order_id="not-a-uuid")),
    ],
    ids=["not-json", "not-an-object", "missing-fields", "unknown-item-type", "bad-uuid"],
)
def test_a_message_outside_the_contract_is_invalid(body: str):
    with pytest.raises(InvalidMessage):
        decode_order_placed(body)
