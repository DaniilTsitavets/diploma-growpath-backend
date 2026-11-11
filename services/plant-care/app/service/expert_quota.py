import uuid
from typing import Protocol


class ExpertQuota(Protocol):
    """Expert quota reservations kept by the entitlement service, keyed by escalation id.

    Both methods raise ExpertQuotaUnavailable when entitlement cannot answer. `reserve_expert` is
    idempotent by id and raises ExpertQuotaExhausted when the quota of the period is used up.
    `release_expert` treats an unknown reservation as released and raises
    ExpertReservationCommitted when entitlement refuses because a ticket already paid for it.
    """

    def reserve_expert(self, escalation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...

    def release_expert(self, escalation_id: uuid.UUID, user_id: uuid.UUID) -> None: ...
