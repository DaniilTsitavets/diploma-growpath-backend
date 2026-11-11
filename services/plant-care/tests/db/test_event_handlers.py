import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import JournalModel
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.event_handlers import EventHandlers
from app.service.events import ItemType, OrderPlaced


def order(**overrides) -> OrderPlaced:
    fields = {
        "order_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "item_type": ItemType.PACK,
        "plant_species": "Basil",
    }
    return OrderPlaced(**{**fields, **overrides})


def journals(engine: Engine) -> list[JournalModel]:
    with Session(engine) as session:
        return list(session.scalars(select(JournalModel)))


def test_a_pack_order_opens_one_unplanted_journal(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    placed = order(plant_species="Chili pepper")

    EventHandlers(unit_of_work).order_placed(placed)

    (journal,) = journals(engine)
    assert (journal.user_id, journal.order_id) == (placed.user_id, placed.order_id)
    assert (journal.species, journal.planted_on) == ("Chili pepper", None)


def test_a_repeated_order_event_keeps_the_first_journal(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    handlers = EventHandlers(unit_of_work)
    placed = order()

    handlers.order_placed(placed)
    handlers.order_placed(placed)

    assert len(journals(engine)) == 1


def test_a_sensor_order_opens_no_journal(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    EventHandlers(unit_of_work).order_placed(order(item_type=ItemType.SENSOR, plant_species=None))

    assert journals(engine) == []


@pytest.mark.parametrize("species", ["  ", "x" * 101])
def test_a_species_the_schema_rejects_is_not_swallowed(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine, species: str
):
    with pytest.raises(IntegrityError):
        EventHandlers(unit_of_work).order_placed(order(plant_species=species))

    assert journals(engine) == []
