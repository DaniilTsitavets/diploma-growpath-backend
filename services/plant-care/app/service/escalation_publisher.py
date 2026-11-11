import logging
from collections.abc import Callable
from datetime import timedelta

from app.service.errors import ContextTooLarge, PublishFailed
from app.service.escalation_events import (
    ContextSource,
    EscalationEvents,
    build_context,
    escalation_requested,
)
from app.service.model import Escalation
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


class EscalationPublisher:
    """Builds `escalation.requested` from the stored rows and sends it to the broker."""

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        events: EscalationEvents,
        expert_reservation_ttl: timedelta,
        ack_margin: timedelta,
        history_limit: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._events = events
        self._ttl = expert_reservation_ttl
        self._ack_margin = ack_margin
        self._history_limit = history_limit

    def publish(self, escalation: Escalation) -> None:
        """Raises PublishFailed or ContextTooLarge; the event is identical on every call."""
        context = build_context(self._load(escalation))
        event = escalation_requested(escalation, context, self._ttl, self._ack_margin)
        self._events.publish_escalation_requested(event)
        logger.info("escalation.requested published", extra={"escalation_id": str(escalation.id)})

    def announce(self, escalation: Escalation) -> None:
        """Best effort right after the commit; the repeater covers a failure."""
        try:
            self.publish(escalation)
        except (PublishFailed, ContextTooLarge):
            logger.exception(
                "escalation.requested not published, the repeater will retry",
                extra={"escalation_id": str(escalation.id)},
            )

    def _load(self, escalation: Escalation) -> ContextSource:
        with self._unit_of_work() as uow:
            update = uow.updates.get_owned(escalation.update_id, escalation.user_id)
            journal = uow.journals.get_owned(escalation.journal_id, escalation.user_id)
            if update is None or journal is None:
                raise RuntimeError(f"escalation {escalation.id} lost its update or journal")
            history = uow.escalation_delivery.history(
                journal.id, update.created_at, self._history_limit
            )
            return ContextSource(
                escalation=escalation,
                species=journal.species,
                update=update,
                photos=uow.photos.list_for_update(update.id),
                diagnosis=uow.diagnoses.get(update.id),
                history=list(reversed(history)),
            )
