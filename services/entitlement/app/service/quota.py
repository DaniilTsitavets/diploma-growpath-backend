import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from app.service.errors import (
    QuotaExhausted,
    ReservationIdInUse,
    ReservationNotFound,
    ReservationUnusable,
)
from app.service.model import Reservation, ReservationStatus, Reserved, Tier
from app.service.ports import ReservationRepository, UnitOfWork
from app.service.tiers import TierPolicy

logger = logging.getLogger(__name__)

RESERVED = ReservationStatus.RESERVED
COMMITTED = ReservationStatus.COMMITTED
RELEASED = ReservationStatus.RELEASED


@dataclass(frozen=True)
class QuotaKind:
    """What differs between the AI and the expert quota."""

    name: str
    repository: Callable[[UnitOfWork], ReservationRepository]
    limits: Mapping[Tier, int]
    ttl: timedelta
    commit_requires_unexpired: bool
    release_from: frozenset[ReservationStatus]


def ai_quota_kind(limits: Mapping[Tier, int], ttl: timedelta) -> QuotaKind:
    """An expired AI reservation is not committed: that diagnosis goes unpaid."""
    return QuotaKind(
        "ai",
        lambda uow: uow.ai_reservations,
        limits,
        ttl,
        commit_requires_unexpired=True,
        release_from=frozenset({RESERVED}),
    )


def expert_quota_kind(limits: Mapping[Tier, int], ttl: timedelta) -> QuotaKind:
    """A ticket exists once `ticket.created` arrives, so it is paid for even after the TTL."""
    return QuotaKind(
        "expert",
        lambda uow: uow.expert_reservations,
        limits,
        ttl,
        commit_requires_unexpired=False,
        release_from=frozenset({RESERVED, COMMITTED}),
    )


class QuotaService:
    def __init__(
        self, kind: QuotaKind, unit_of_work: Callable[[], UnitOfWork], tiers: TierPolicy
    ) -> None:
        self._kind = kind
        self._unit_of_work = unit_of_work
        self._tiers = tiers

    def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> Reserved:
        with self._unit_of_work() as uow:
            reservations = self._kind.repository(uow)
            reservations.lock_user(user_id)
            existing = reservations.get(reservation_id)
            if existing is not None:
                return Reserved(self._replayed(existing, user_id), created=False)

            self._ensure_below_ceiling(uow, reservations, user_id)
            created = reservations.insert(reservation_id, user_id, self._kind.ttl)
            if created is None:
                # The id is global but the lock is per user: another user's request won the id.
                return Reserved(
                    self._replayed(self._reread(reservations, reservation_id), user_id), False
                )
            uow.commit()
        logger.info(
            "quota reserved",
            extra={"quota": self._kind.name, "reservation_id": str(reservation_id)},
        )
        return Reserved(created, created=True)

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> Reservation:
        return self._transition(
            reservation_id,
            user_id,
            target=COMMITTED,
            sources=frozenset({RESERVED}),
            unexpired_only=self._kind.commit_requires_unexpired,
        )

    def release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> Reservation:
        return self._transition(
            reservation_id,
            user_id,
            target=RELEASED,
            sources=self._kind.release_from,
            unexpired_only=False,
        )

    def _ensure_below_ceiling(
        self, uow: UnitOfWork, reservations: ReservationRepository, user_id: uuid.UUID
    ) -> None:
        tier = self._tiers.tier_for(uow.placed_orders.window_total(user_id))
        if reservations.count_used(user_id) >= self._kind.limits[tier]:
            raise QuotaExhausted

    @staticmethod
    def _reread(reservations: ReservationRepository, reservation_id: uuid.UUID) -> Reservation:
        reservation = reservations.get(reservation_id)
        if reservation is None:
            raise RuntimeError("a reservation conflicted on its id but cannot be read")
        return reservation

    @staticmethod
    def _replayed(existing: Reservation, user_id: uuid.UUID) -> Reservation:
        if existing.user_id != user_id:
            raise ReservationIdInUse
        usable = existing.status is COMMITTED or (
            existing.status is RESERVED and existing.unexpired
        )
        if not usable:
            raise ReservationUnusable(existing.status, expired=existing.status is RESERVED)
        return existing

    def _transition(
        self,
        reservation_id: uuid.UUID,
        user_id: uuid.UUID,
        target: ReservationStatus,
        sources: frozenset[ReservationStatus],
        unexpired_only: bool,
    ) -> Reservation:
        with self._unit_of_work() as uow:
            reservations = self._kind.repository(uow)
            moved = reservations.transition(
                reservation_id, user_id, target, sources, unexpired_only
            )
            if moved is not None:
                uow.commit()
                return moved
            current = reservations.get_owned(reservation_id, user_id)
        if current is None:
            raise ReservationNotFound
        if current.status is target:
            return current
        raise ReservationUnusable(
            current.status, expired=current.status is RESERVED and not current.unexpired
        )
