import uuid
from collections.abc import Callable
from datetime import datetime

from app.service.model import Entitlement, QuotaUsage
from app.service.ports import UnitOfWork
from app.service.quota import QuotaKind
from app.service.tiers import TierPolicy


class EntitlementService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        tiers: TierPolicy,
        ai: QuotaKind,
        expert: QuotaKind,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._tiers = tiers
        self._ai = ai
        self._expert = expert

    def get(self, user_id: uuid.UUID) -> Entitlement:
        with self._unit_of_work() as uow:
            total = uow.placed_orders.window_total(user_id)
            tier = self._tiers.tier_for(total)
            return Entitlement(
                tier=tier,
                tier_expires_at=self._tier_expires_at(uow, user_id, total),
                ai=QuotaUsage(self._ai.repository(uow).count_used(user_id), self._ai.limits[tier]),
                expert=QuotaUsage(
                    self._expert.repository(uow).count_used(user_id), self._expert.limits[tier]
                ),
            )

    def _tier_expires_at(self, uow: UnitOfWork, user_id: uuid.UUID, total: int) -> datetime | None:
        threshold = self._tiers.threshold_of(self._tiers.tier_for(total))
        if threshold is None:
            return None
        return uow.placed_orders.tier_expires_at(user_id, total, threshold)
