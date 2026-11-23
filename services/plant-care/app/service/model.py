import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Lighting(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    BRIGHT_INDIRECT = "bright_indirect"
    DIRECT_SUN = "direct_sun"


class YesNo(StrEnum):
    YES = "yes"
    NO = "no"


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class WateringMethod(StrEnum):
    THROUGH_DRAINAGE = "through_drainage"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class PotDiameterClass(StrEnum):
    LT_10 = "lt_10"
    FROM_10_TO_15 = "10_15"
    FROM_15_TO_20 = "15_20"
    FROM_20_TO_30 = "20_30"
    GT_30 = "gt_30"
    UNKNOWN = "unknown"


class DryDepth(StrEnum):
    NOT_DRY = "not_dry"
    ABOUT_2_3_CM = "about_2_3cm"
    ABOUT_5_CM = "about_5cm"
    WHOLE_ROOT_BALL_DRY = "whole_root_ball_dry"
    UNKNOWN = "unknown"


class PotWeightRelative(StrEnum):
    ABOUT_SAME = "about_same"
    NOTICEABLY_LIGHTER = "noticeably_lighter"
    VERY_LIGHT = "very_light"
    UNKNOWN = "unknown"


class SensorUnit(StrEnum):
    VWC_PCT = "vwc_pct"
    RAW = "raw"


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


class Tier(StrEnum):
    STARTER = "starter"
    HOBBYIST = "hobbyist"
    PREMIUM = "premium"


class ModelEffort(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"


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
    lighting: Lighting
    last_watered_on: date | None = None
    watering_method: WateringMethod = WateringMethod.UNKNOWN
    pot_diameter_class: PotDiameterClass = PotDiameterClass.UNKNOWN
    drainage_holes: Answer = Answer.UNKNOWN
    standing_water_over_1h: Answer = Answer.UNKNOWN
    dry_depth: DryDepth = DryDepth.UNKNOWN
    pot_weight_relative: PotWeightRelative = PotWeightRelative.UNKNOWN
    pot_weight_after_watering_g: Decimal | None = None
    pot_weight_now_g: Decimal | None = None
    repotted_or_moved: YesNo | None = None
    sensor_reading: Decimal | None = None
    sensor_unit: SensorUnit | None = None
    air_temp_c: Decimal | None = None
    air_rh_pct: Decimal | None = None
    air_measured_at: datetime | None = None
    air_temp_min_c: Decimal | None = None
    air_temp_max_c: Decimal | None = None
    air_rh_min_pct: Decimal | None = None
    air_rh_max_pct: Decimal | None = None


@dataclass(frozen=True)
class UpdateRecord(NewUpdate):
    created_at: datetime = field(kw_only=True)


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
class Candidate:
    cause: str
    rationale: str


@dataclass(frozen=True)
class CandidateRecord:
    rank: int
    cause: str
    rationale: str


@dataclass(frozen=True)
class Diagnosis:
    update_id: uuid.UUID
    ai_reservation_id: uuid.UUID
    summary: DiagnosisSummary
    candidates: list[CandidateRecord]


@dataclass(frozen=True)
class DiagnosisResult:
    """What an attempt stores when the model answered: a question to the user or a conclusion."""

    status: DiagnosisStatus
    has_dominant_candidate: bool
    candidates: list[Candidate]
    recommendation: str | None
    clarifying_question: str | None
    roots_photo_requested: bool
    escalation_offered: bool


@dataclass(frozen=True)
class EscalationSummary:
    id: uuid.UUID
    status: EscalationStatus
    answer_text: str | None
    answered_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class NewEscalation:
    id: uuid.UUID
    update_id: uuid.UUID
    user_question: str | None


@dataclass(frozen=True)
class Escalation:
    id: uuid.UUID
    update_id: uuid.UUID
    journal_id: uuid.UUID
    user_id: uuid.UUID
    status: EscalationStatus
    ticket_id: uuid.UUID | None
    user_question: str | None
    answer_text: str | None
    answered_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class HistoryEntry:
    """An earlier update of a journal and how its diagnosis went."""

    created_at: datetime
    diagnosis_status: DiagnosisStatus | None
    top_cause: str | None


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
