import logging
import uuid
from collections.abc import Callable

from app.service.consumer import InvalidMessage
from app.service.events import ItemType, OrderPlaced
from app.service.model import NewJournal
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


class EventHandlers:
    """Idempotent reactions to the events of other services.

    Constraint violations are not caught here: they are bugs, and the message has to reach the DLQ.
    """

    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def order_placed(self, event: OrderPlaced) -> None:
        """A pack order opens a journal; a sensor order has nothing to plant."""
        if event.item_type is not ItemType.PACK:
            logger.info("order.placed ignored, not a pack", extra={"order_id": str(event.order_id)})
            return
        if event.plant_species is None:
            raise InvalidMessage("a pack order has no plant species")
        journal = NewJournal(uuid.uuid4(), event.user_id, event.order_id, event.plant_species)
        with self._unit_of_work() as uow:
            added = uow.journals.add(journal)
            uow.commit()
        logger.info(
            "order.placed applied" if added else "order.placed duplicate ignored",
            extra={"order_id": str(event.order_id)},
        )
