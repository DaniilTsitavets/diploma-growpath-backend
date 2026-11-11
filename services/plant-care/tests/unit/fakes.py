import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Self

from app.service.errors import AiQuotaExhausted, AiQuotaUnavailable
from app.service.model import (
    DiagnosisStatus,
    DiagnosisSummary,
    Drainage,
    FeedCursor,
    FeedItem,
    FingerTest,
    Journal,
    Lighting,
    NewJournal,
    NewUpdate,
    Photo,
    PhotoKind,
    PotWeight,
    UpdateRecord,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 11)


class FakeJournals:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, Journal] = {}

    def seed(self, user_id: uuid.UUID, **overrides) -> Journal:
        fields = {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "order_id": uuid.uuid4(),
            "species": "Basil",
            "planted_on": None,
            "created_at": NOW + timedelta(minutes=len(self.rows)),
        }
        journal = Journal(**{**fields, **overrides})
        self.rows[journal.id] = journal
        return journal

    def add(self, journal: NewJournal) -> bool:
        if any(row.order_id == journal.order_id for row in self.rows.values()):
            return False
        self.seed(
            journal.user_id, id=journal.id, order_id=journal.order_id, species=journal.species
        )
        return True

    def get_owned(self, journal_id: uuid.UUID, user_id: uuid.UUID) -> Journal | None:
        journal = self.rows.get(journal_id)
        return journal if journal is not None and journal.user_id == user_id else None

    def list_for_user(self, user_id: uuid.UUID) -> list[Journal]:
        owned = [row for row in self.rows.values() if row.user_id == user_id]
        return sorted(owned, key=lambda row: row.created_at, reverse=True)

    def set_planted_on(
        self, journal_id: uuid.UUID, user_id: uuid.UUID, planted_on: date
    ) -> Journal | None:
        journal = self.get_owned(journal_id, user_id)
        if journal is None:
            return None
        planted = Journal(**{**vars(journal), "planted_on": planted_on})
        self.rows[journal_id] = planted
        return planted


class FakeDiagnoses:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, uuid.UUID] = {}

    def add_pending(self, update_id: uuid.UUID, ai_reservation_id: uuid.UUID) -> bool:
        if update_id in self.rows:
            return False
        self.rows[update_id] = ai_reservation_id
        return True


class FakeUpdates:
    def __init__(self, diagnoses: FakeDiagnoses, journals: FakeJournals) -> None:
        self.rows: dict[uuid.UUID, UpdateRecord] = {}
        self._diagnoses = diagnoses
        self._journals = journals
        self.feed_calls: list[tuple[uuid.UUID, int, FeedCursor | None]] = []

    def seed(self, journal_id: uuid.UUID, created_at: datetime | None = None) -> UpdateRecord:
        update = self._record(
            NewUpdate(
                uuid.uuid4(),
                journal_id,
                None,
                None,
                Drainage.GOOD,
                Lighting.LOW,
                FingerTest.DRY,
                PotWeight.LIGHTER,
                None,
            ),
            created_at or NOW + timedelta(minutes=len(self.rows)),
        )
        self.rows[update.id] = update
        return update

    @staticmethod
    def _record(update: NewUpdate, created_at: datetime) -> UpdateRecord:
        return UpdateRecord(created_at=created_at, **vars(update))

    def add(self, update: NewUpdate) -> bool:
        if update.id in self.rows:
            return False
        self.rows[update.id] = self._record(update, NOW + timedelta(minutes=len(self.rows)))
        return True

    def item(self, update_id: uuid.UUID) -> FeedItem | None:
        update = self.rows.get(update_id)
        if update is None:
            return None
        diagnosis = None
        if update_id in self._diagnoses.rows:
            diagnosis = DiagnosisSummary(
                DiagnosisStatus.PENDING, None, None, None, None, False, None, False, False
            )
        return FeedItem(update, diagnosis, None)

    def get_owned(self, update_id: uuid.UUID, user_id: uuid.UUID) -> UpdateRecord | None:
        update = self.rows.get(update_id)
        if update is None or self._journals.get_owned(update.journal_id, user_id) is None:
            return None
        return update

    def feed(self, journal_id: uuid.UUID, limit: int, before: FeedCursor | None) -> list[FeedItem]:
        self.feed_calls.append((journal_id, limit, before))
        rows = [row for row in self.rows.values() if row.journal_id == journal_id]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        if before is not None:
            rows = [
                row for row in rows if (row.created_at, row.id) < (before.created_at, before.id)
            ]
        return [FeedItem(row, None, None) for row in rows[:limit]]


class FakePhotos:
    def __init__(self) -> None:
        self.rows: list[Photo] = []

    def add(self, update_id: uuid.UUID, kind: PhotoKind, storage_key: str) -> bool:
        if any(row.update_id == update_id and row.kind == kind for row in self.rows):
            return False
        self.rows.append(Photo(update_id, kind, storage_key))
        return True

    def list_for_update(self, update_id: uuid.UUID) -> list[Photo]:
        return [row for row in self.rows if row.update_id == update_id]


class FakeUnitOfWork:
    """Shares one set of fake repositories between all units of a test."""

    def __init__(self) -> None:
        self.journals = FakeJournals()
        self.diagnoses = FakeDiagnoses()
        self.updates = FakeUpdates(self.diagnoses, self.journals)
        self.photos = FakePhotos()
        self.commits = 0
        self.fail_commit_with: Exception | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def commit(self) -> None:
        if self.fail_commit_with is not None:
            raise self.fail_commit_with
        self.commits += 1


class FakeAiQuota:
    def __init__(self) -> None:
        self.reserved: list[uuid.UUID] = []
        self.released: list[uuid.UUID] = []
        self.committed: list[uuid.UUID] = []
        self.exhausted = False
        self.unavailable = False
        self.release_unavailable = False

    def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        if self.unavailable:
            raise AiQuotaUnavailable
        if self.exhausted:
            raise AiQuotaExhausted
        self.reserved.append(reservation_id)

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.committed.append(reservation_id)

    def release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        if self.release_unavailable:
            raise AiQuotaUnavailable
        self.released.append(reservation_id)
