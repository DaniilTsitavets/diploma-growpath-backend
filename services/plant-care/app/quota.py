import uuid

from app.service.errors import AiQuotaUnavailable


class UnconfiguredAiQuota:
    """Placeholder until the entitlement client exists: every call reports "unavailable".

    Updates are then accepted in `diagnosis_pending`, the documented fallback.
    """

    def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        raise AiQuotaUnavailable

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        raise AiQuotaUnavailable

    def release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        raise AiQuotaUnavailable
