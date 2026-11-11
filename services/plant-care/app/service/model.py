import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Drainage(StrEnum):
    GOOD = "good"
    POOR = "poor"
    NONE = "none"


class Lighting(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    BRIGHT_INDIRECT = "bright_indirect"
    DIRECT_SUN = "direct_sun"


class FingerTest(StrEnum):
    DRY = "dry"
    SLIGHTLY_MOIST = "slightly_moist"
    WET = "wet"


class PotWeight(StrEnum):
    LIGHTER = "lighter"
    AS_EXPECTED = "as_expected"
    HEAVIER = "heavier"


class PhotoKind(StrEnum):
    OVERVIEW = "overview"
    SOIL = "soil"
    SYMPTOM_CLOSEUP = "symptom_closeup"
    ROOTS_DRAINAGE = "roots_drainage"


class DiagnosisStatus(StrEnum):
    PENDING = "diagnosis_pending"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    COMPLETED = "completed"
    DEGRADED = "degraded"


class EscalationStatus(StrEnum):
    REQUESTED = "requested"
    TICKET_OPEN = "ticket_open"
    ANSWERED = "answered"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NewJournal:
    id: uuid.UUID
    user_id: uuid.UUID
    order_id: uuid.UUID
    species: str


@dataclass(frozen=True)
class Journal:
    id: uuid.UUID
    user_id: uuid.UUID
    order_id: uuid.UUID
    species: str
    planted_on: date | None
    created_at: datetime


@dataclass(frozen=True)
class NewUpdate:
    id: uuid.UUID
    journal_id: uuid.UUID
    last_watered_on: date | None
    watering_interval_days: int | None
    drainage: Drainage
    lighting: Lighting
    finger_test: FingerTest
    pot_weight: PotWeight
    sensor_reading: Decimal | None


@dataclass(frozen=True)
class UpdateRecord:
    id: uuid.UUID
    journal_id: uuid.UUID
    last_watered_on: date | None
    watering_interval_days: int | None
    drainage: Drainage
    lighting: Lighting
    finger_test: FingerTest
    pot_weight: PotWeight
    sensor_reading: Decimal | None
    created_at: datetime


@dataclass(frozen=True)
class DiagnosisSummary:
    status: DiagnosisStatus
    model_effort: str | None
    has_dominant_candidate: bool | None
    clarifying_question: str | None
    clarifying_answer: str | None
    roots_photo_requested: bool
    recommendation: str | None
    escalation_offered: bool
    escalation_declined: bool


@dataclass(frozen=True)
class EscalationSummary:
    id: uuid.UUID
    status: EscalationStatus
    answer_text: str | None
    answered_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class FeedItem:
    update: UpdateRecord
    diagnosis: DiagnosisSummary | None
    escalation: EscalationSummary | None


@dataclass(frozen=True)
class FeedCursor:
    """Position of the last item of a page; the next page continues strictly before it."""

    created_at: datetime
    id: uuid.UUID


@dataclass(frozen=True)
class Photo:
    update_id: uuid.UUID
    kind: PhotoKind
    storage_key: str
