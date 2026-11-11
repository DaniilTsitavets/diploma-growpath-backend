import uuid
from datetime import date, datetime, timedelta
from typing import Protocol, Self

from app.service.model import (
    Diagnosis,
    DiagnosisResult,
    Escalation,
    FeedCursor,
    FeedItem,
    HistoryEntry,
    Journal,
    ModelEffort,
    NewEscalation,
    NewJournal,
    NewUpdate,
    Photo,
    PhotoKind,
    UpdateRecord,
)


class JournalRepository(Protocol):
    def add(self, journal: NewJournal) -> bool:
        """Store the journal; False when the order already has one (a repeated event)."""
        ...

    def get_owned(self, journal_id: uuid.UUID, user_id: uuid.UUID) -> Journal | None: ...

    def list_for_user(self, user_id: uuid.UUID) -> list[Journal]:
        """Newest first."""
        ...

    def set_planted_on(
        self, journal_id: uuid.UUID, user_id: uuid.UUID, planted_on: date
    ) -> Journal | None:
        """None when the user has no such journal."""
        ...


class UpdateRepository(Protocol):
    def add(self, update: NewUpdate) -> bool:
        """Store the update; False when its id already exists (a retried request)."""
        ...

    def item(self, update_id: uuid.UUID) -> FeedItem | None:
        """The update with its diagnosis and latest escalation, as the feed shows it."""
        ...

    def get_owned(self, update_id: uuid.UUID, user_id: uuid.UUID) -> UpdateRecord | None:
        """The update of a journal that belongs to the user."""
        ...

    def feed(self, journal_id: uuid.UUID, limit: int, before: FeedCursor | None) -> list[FeedItem]:
        """Newest first; with `before`, only the items strictly older than the cursor."""
        ...


class DiagnosisRepository(Protocol):
    def add_pending(self, update_id: uuid.UUID, ai_reservation_id: uuid.UUID) -> bool:
        """Create the diagnosis in `diagnosis_pending`; False when the update already has one."""
        ...

    def get(self, update_id: uuid.UUID) -> Diagnosis | None: ...

    def claim_first_attempt(
        self, update_id: uuid.UUID, reservation_id: uuid.UUID, effort: ModelEffort
    ) -> bool:
        """Set the effort of a pending diagnosis nobody has started; False when it was taken."""
        ...

    def claim_retry(
        self,
        update_id: uuid.UUID,
        old_reservation_id: uuid.UUID,
        new_reservation_id: uuid.UUID,
        effort: ModelEffort,
    ) -> bool:
        """Start a new attempt on a pending or degraded diagnosis that still holds the old id."""
        ...

    def answer_clarification(self, update_id: uuid.UUID, answer: str) -> bool:
        """`awaiting_clarification` to `diagnosis_pending`; False when no answer is awaited."""
        ...

    def record_result(
        self, update_id: uuid.UUID, reservation_id: uuid.UUID, result: DiagnosisResult
    ) -> bool:
        """Store the result and its candidates; False when another attempt owns the diagnosis."""
        ...

    def mark_degraded(
        self, update_id: uuid.UUID, reservation_id: uuid.UUID, recommendation: str
    ) -> bool:
        """Finish the attempt with a fallback recommendation; False when it lost the diagnosis."""
        ...

    def decline_escalation(self, update_id: uuid.UUID) -> bool:
        """Record that the user declined the offered expert; False when none is on offer."""
        ...


class PhotoRepository(Protocol):
    def add(self, update_id: uuid.UUID, kind: PhotoKind, storage_key: str) -> bool:
        """False when the update already has a photo of this kind; the stored key is kept."""
        ...

    def list_for_update(self, update_id: uuid.UUID) -> list[Photo]: ...


class EscalationRepository(Protocol):
    def add(self, escalation: NewEscalation) -> bool:
        """Store the escalation; False when the update already has a non-cancelled one."""
        ...

    def get(self, escalation_id: uuid.UUID) -> Escalation | None: ...

    def get_active(self, update_id: uuid.UUID) -> Escalation | None:
        """The update's escalation that is not cancelled."""
        ...

    def latest(self, update_id: uuid.UUID) -> Escalation | None: ...


class EscalationDeliveryRepository(Protocol):
    """What the escalation repeater and the event builder read and change."""

    def claim_due(self, grace: timedelta, ttl: timedelta, limit: int) -> list[Escalation]:
        """Lock `requested` escalations older than `grace` and younger than `ttl`, oldest first.

        Rows another worker has locked are skipped; the locks last until the unit ends.
        """
        ...

    def claim_expired(self, ttl: timedelta, limit: int) -> list[Escalation]:
        """Lock `requested` escalations that reached `ttl`, oldest first, skipping locked rows."""
        ...

    def cancel_requested(self, escalation_id: uuid.UUID) -> bool:
        """`requested` to `cancelled`; False when a ticket event moved it first."""
        ...

    def history(self, journal_id: uuid.UUID, before: datetime, limit: int) -> list[HistoryEntry]:
        """The journal's updates created before `before`, newest first."""
        ...


class UnitOfWork(Protocol):
    """One business action in one transaction: leaving the block without commit rolls back."""

    journals: JournalRepository
    updates: UpdateRepository
    diagnoses: DiagnosisRepository
    photos: PhotoRepository
    escalations: EscalationRepository
    escalation_delivery: EscalationDeliveryRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def commit(self) -> None: ...
