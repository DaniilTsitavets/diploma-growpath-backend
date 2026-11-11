import uuid
from dataclasses import replace
from datetime import timedelta

from app.service.errors import ExpertQuotaExhausted, ExpertQuotaUnavailable
from app.service.model import Escalation, EscalationStatus, NewEscalation
from tests.unit.fakes import NOW, FakeJournals, FakeUnitOfWork, FakeUpdates


class FakeEscalations:
    """Mirrors the partial unique index: one non-cancelled escalation per update."""

    def __init__(self, updates: FakeUpdates, journals: FakeJournals) -> None:
        self.rows: dict[uuid.UUID, Escalation] = {}
        self._updates = updates
        self._journals = journals

    def seed(self, update_id: uuid.UUID, **overrides) -> Escalation:
        update = self._updates.rows[update_id]
        journal = self._journals.rows[update.journal_id]
        fields = {
            "id": uuid.uuid4(),
            "update_id": update_id,
            "journal_id": journal.id,
            "user_id": journal.user_id,
            "status": EscalationStatus.REQUESTED,
            "ticket_id": None,
            "user_question": None,
            "answer_text": None,
            "answered_at": None,
            "created_at": NOW + timedelta(minutes=len(self.rows)),
        }
        escalation = Escalation(**{**fields, **overrides})
        self.rows[escalation.id] = escalation
        return escalation

    def add(self, escalation: NewEscalation) -> bool:
        if self.get_active(escalation.update_id) is not None:
            return False
        self.seed(escalation.update_id, id=escalation.id, user_question=escalation.user_question)
        return True

    def get(self, escalation_id: uuid.UUID) -> Escalation | None:
        return self.rows.get(escalation_id)

    def get_active(self, update_id: uuid.UUID) -> Escalation | None:
        for row in self.rows.values():
            if row.update_id == update_id and row.status is not EscalationStatus.CANCELLED:
                return row
        return None

    def latest(self, update_id: uuid.UUID) -> Escalation | None:
        rows = [row for row in self.rows.values() if row.update_id == update_id]
        return max(rows, key=lambda row: row.created_at, default=None)

    def change(self, escalation_id: uuid.UUID, **fields: object) -> None:
        self.rows[escalation_id] = replace(self.rows[escalation_id], **fields)


class EscalationUnitOfWork(FakeUnitOfWork):
    """Rolls the escalation rows back when a block ends without a successful commit."""

    def __init__(self) -> None:
        super().__init__()
        self.escalations = FakeEscalations(self.updates, self.journals)
        self._snapshot: dict[uuid.UUID, Escalation] = {}
        self._committed = False

    def __enter__(self):
        self._snapshot = dict(self.escalations.rows)
        self._committed = False
        return self

    def __exit__(self, *exc_info: object) -> None:
        if not self._committed:
            self.escalations.rows = self._snapshot

    def commit(self) -> None:
        super().commit()
        self._committed = True


class FakeExpertQuota:
    def __init__(self) -> None:
        self.reserved: list[uuid.UUID] = []
        self.released: list[uuid.UUID] = []
        self.users: list[uuid.UUID] = []
        self.exhausted = False
        self.reserve_unavailable = False
        self.release_fails_with: Exception | None = None
        self.log: list[str] = []

    def reserve_expert(self, escalation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.log.append("reserve")
        if self.exhausted:
            raise ExpertQuotaExhausted
        if self.reserve_unavailable:
            raise ExpertQuotaUnavailable
        self.reserved.append(escalation_id)
        self.users.append(user_id)

    def release_expert(self, escalation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.log.append("release")
        if self.release_fails_with is not None:
            raise self.release_fails_with
        self.released.append(escalation_id)


class FakeAnnouncer:
    def __init__(self) -> None:
        self.announced: list[Escalation] = []

    def announce(self, escalation: Escalation) -> None:
        self.announced.append(escalation)
