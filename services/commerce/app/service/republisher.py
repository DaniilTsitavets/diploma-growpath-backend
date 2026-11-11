import logging
from collections.abc import Callable
from datetime import timedelta

from growpath_observability import set_correlation_id

from app.service.errors import PublishFailed
from app.service.events import order_placed
from app.service.ports import OrderEvents, UnitOfWork

logger = logging.getLogger(__name__)


class OrderRepublisher:
    """Sends `order.placed` for paid orders whose event never got out (broker down, crash).

    Delivery is at least once: the request path and this loop may publish the same order, and
    consumers drop duplicates by event id.
    """

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        events: OrderEvents,
        grace: timedelta,
        batch_size: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._events = events
        self._grace = grace
        self._batch_size = batch_size

    def run_once(self) -> int:
        """One batch in one transaction; returns how many events were published."""
        published = 0
        with self._unit_of_work() as uow:
            for order in uow.orders.claim_unpublished(self._grace, self._batch_size):
                item = uow.catalog.get(order.item_id)
                if item is None:
                    logger.error(
                        "order refers to a missing item", extra={"order_id": str(order.id)}
                    )
                    continue
                set_correlation_id(str(order.id))
                try:
                    self._events.publish_order_placed(order_placed(order, item))
                except PublishFailed:
                    logger.exception(
                        "republishing order.placed failed, will retry",
                        extra={"order_id": str(order.id)},
                    )
                    continue
                uow.orders.mark_published(order.id)
                logger.info("order.placed republished", extra={"order_id": str(order.id)})
                published += 1
            uow.commit()
        return published
