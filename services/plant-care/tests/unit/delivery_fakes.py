import uuid
from datetime import datetime, timedelta

from app.service.errors import PublishFailed
from app.service.escalation_events import EscalationRequested
from app.service.model import Escalation, EscalationStatus, HistoryEntry
from tests.unit.escalation_fakes import EscalationUnitOfWork, FakeEscalations
from tests.unit.fakes import NOW


class FakeEscalationDelivery:
    """Mirrors the claim queries; `clock` stands for the database `now()`."""

    def __init__(self, escalations: FakeEscalations) -> None:
        self._escalations = escalations
        self.clock = NOW
        self.history_entries: list[HistoryEntry] = []
        self.history_calls: list[tuple[uuid.UUID, datetime, int]] = []
        self.claim_limits: list[int] = []

    def claim_due(self, grace: timedelta, ttl: timedelta, limit: int) -> list[Escalation]:
        self.claim_limits.append(limit)
        return self._requested(
            lambda row: self.clock - ttl < row.created_at < self.clock - grace, limit
        )

    def claim_expired(self, ttl: timedelta, limit: int) -> list[Escalation]:
        self.claim_limits.append(limit)
        return self._requested(lambda row: row.created_at <= self.clock - ttl, limit)

    def cancel_requested(self, escalation_id: uuid.UUID) -> bool:
        row = self._escalations.rows.get(escalation_id)
        if row is None or row.status is not EscalationStatus.REQUESTED:
            return False
        self._escalations.change(escalation_id, status=EscalationStatus.CANCELLED)
        return True

    def history(self, journal_id: uuid.UUID, before: datetime, limit: int) -> list[HistoryEntry]:
        self.history_calls.append((journal_id, before, limit))
        older = [e for e in self.history_entries if e.created_at < before]
        return sorted(older, key=lambda e: e.created_at, reverse=True)[:limit]

    def _requested(self, matches, limit: int) -> list[Escalation]:
        rows = [
            row
            for row in self._escalations.rows.values()
            if row.status is EscalationStatus.REQUESTED and matches(row)
        ]
        return sorted(rows, key=lambda row: row.created_at)[:limit]


class DeliveryUnitOfWork(EscalationUnitOfWork):
    def __init__(self) -> None:
        super().__init__()
        self.escalation_delivery = FakeEscalationDelivery(self.escalations)


class FakeEscalationEvents:
    def __init__(self) -> None:
        self.published: list[EscalationRequested] = []
        self.fail = False

    def publish_escalation_requested(self, event: EscalationRequested) -> None:
        if self.fail:
            raise PublishFailed("broker down")
        self.published.append(event)
