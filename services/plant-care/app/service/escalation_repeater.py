import logging
from collections.abc import Callable
from datetime import timedelta

from growpath_observability import set_correlation_id

from app.service.errors import (
    ContextTooLarge,
    ExpertQuotaUnavailable,
    ExpertReservationCommitted,
    PublishFailed,
)
from app.service.escalation_publisher import EscalationPublisher
from app.service.expert_quota import ExpertQuota
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


class EscalationRepeater:
    """Keeps `requested` escalations moving until a ticket confirms them or their TTL ends.

    `requested` is the "event not delivered yet" marker. Until `expert_reservation_ttl` passes the
    event is sent again; then the quota reservation is released and the escalation cancelled, so
    the update can be escalated again.
    """

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        publisher: EscalationPublisher,
        expert_quota: ExpertQuota,
        grace: timedelta,
        expert_reservation_ttl: timedelta,
        batch_size: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._publisher = publisher
        self._expert_quota = expert_quota
        self._grace = grace
        self._ttl = expert_reservation_ttl
        self._batch_size = batch_size

    def run_once(self) -> None:
        self.republish_due()
        self.expire_stale()

    def republish_due(self) -> int:
        """One batch in one transaction; returns how many events were sent."""
        sent = 0
        with self._unit_of_work() as uow:
            for escalation in uow.escalation_delivery.claim_due(
                self._grace, self._ttl, self._batch_size
            ):
                set_correlation_id(str(escalation.id))
                try:
                    self._publisher.publish(escalation)
                except (PublishFailed, ContextTooLarge):
                    logger.exception(
                        "republishing escalation.requested failed, will retry",
                        extra={"escalation_id": str(escalation.id)},
                    )
                    continue
                sent += 1
        return sent

    def expire_stale(self) -> int:
        """Release the reservation first, then cancel; returns how many were cancelled.

        A refused release means the reservation is committed, so a ticket exists: the escalation
        stays `requested` until `ticket.created` arrives.
        """
        cancelled = 0
        with self._unit_of_work() as uow:
            for escalation in uow.escalation_delivery.claim_expired(self._ttl, self._batch_size):
                set_correlation_id(str(escalation.id))
                try:
                    self._expert_quota.release_expert(escalation.id, escalation.user_id)
                except ExpertReservationCommitted:
                    logger.info(
                        "expired escalation kept, its reservation is committed",
                        extra={"escalation_id": str(escalation.id)},
                    )
                    continue
                except ExpertQuotaUnavailable:
                    logger.warning(
                        "expired escalation kept, the reservation could not be released",
                        extra={"escalation_id": str(escalation.id)},
                    )
                    continue
                if uow.escalation_delivery.cancel_requested(escalation.id):
                    cancelled += 1
                    logger.info(
                        "escalation cancelled, no ticket within the reservation TTL",
                        extra={"escalation_id": str(escalation.id)},
                    )
            uow.commit()
        return cancelled
