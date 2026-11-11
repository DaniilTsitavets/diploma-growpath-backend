import logging
import uuid
from collections.abc import Callable

from app.service.errors import ReservationConflict, ReservationNotFound
from app.service.events import OrderPlaced, TicketCancelled, TicketCreated
from app.service.model import NewPlacedOrder, Reservation
from app.service.ports import UnitOfWork
from app.service.quota import QuotaService

logger = logging.getLogger(__name__)


class EventHandlers:
    """Idempotent reactions to the events of other services.

    Constraint violations are not caught here: they are bugs, and the message has to reach the DLQ.
    """

    def __init__(self, unit_of_work: Callable[[], UnitOfWork], expert_quota: QuotaService) -> None:
        self._unit_of_work = unit_of_work
        self._expert_quota = expert_quota

    def order_placed(self, event: OrderPlaced) -> None:
        order = NewPlacedOrder(
            event.order_id, event.user_id, event.total_paid_cents, event.placed_at
        )
        with self._unit_of_work() as uow:
            added = uow.placed_orders.add(order)
            uow.commit()
        logger.info(
            "order.placed applied" if added else "order.placed duplicate ignored",
            extra={"order_id": str(event.order_id)},
        )

    def ticket_created(self, event: TicketCreated) -> None:
        self._apply_to_reservation("ticket.created", event, self._expert_quota.commit)

    def ticket_cancelled(self, event: TicketCancelled) -> None:
        self._apply_to_reservation("ticket.cancelled", event, self._expert_quota.release_by_event)

    @staticmethod
    def _apply_to_reservation(
        event_type: str,
        event: TicketCreated | TicketCancelled,
        action: Callable[[uuid.UUID, uuid.UUID], Reservation],
    ) -> None:
        try:
            action(event.escalation_id, event.user_id)
        except (ReservationConflict, ReservationNotFound) as error:
            logger.warning(
                "event ignored, the reservation cannot make this transition",
                extra={
                    "event_type": event_type,
                    "escalation_id": str(event.escalation_id),
                    "reason": str(error),
                },
            )
            return
        logger.info(
            "ticket event applied",
            extra={"event_type": event_type, "escalation_id": str(event.escalation_id)},
        )
