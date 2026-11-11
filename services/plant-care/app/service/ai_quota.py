import uuid
from typing import Protocol

from app.service.model import Tier


class AiQuota(Protocol):
    """Reservations of the AI quota kept by the entitlement service.

    Every method raises AiQuotaUnavailable when entitlement cannot answer. `reserve` is idempotent
    by id; it raises AiQuotaExhausted when the quota of the period is used up and
    ReservationUnusable when the id belongs to a released or expired reservation. `commit` raises
    ReservationUnusable for a reservation that can no longer be committed; `release` treats an
    unknown reservation as released.
    """

    def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...

    def release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...


class TierLookup(Protocol):
    def tier_of(self, user_id: uuid.UUID) -> Tier:
        """The tier of the user. Raises AiQuotaUnavailable when entitlement cannot answer."""
        ...
