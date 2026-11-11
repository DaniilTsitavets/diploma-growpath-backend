import uuid
from typing import Protocol


class AiQuota(Protocol):
    """Reservations of the AI quota kept by the entitlement service.

    Every method raises AiQuotaUnavailable when entitlement cannot answer; `reserve` raises
    AiQuotaExhausted when the quota of the period is used up.
    """

    def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...

    def release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...
