import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.service.checkpoints import Checkpoint, CheckpointState
from app.service.journals import JournalPage, JournalView
from app.service.model import (
    Answer,
    Diagnosis,
    DiagnosisStatus,
    DiagnosisSummary,
    DryDepth,
    Escalation,
    EscalationStatus,
    EscalationSummary,
    FeedItem,
    Lighting,
    NewUpdate,
    PotDiameterClass,
    PotWeightRelative,
    SensorUnit,
    WateringMethod,
    YesNo,
)
from app.service.updates import FeedPage
from app.service.watering_types import DepletionSource, PSource
from app.service.watering_types import Flag as WateringFlag
from app.service.watering_types import Reliability as WateringReliability
from app.service.watering_types import Status as WateringStatus

# Measurements are Decimal, so responses carry them as strings and no precision is lost.
SensorReading = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
PotWeightGrams = Annotated[Decimal, Field(gt=0, le=200000, max_digits=10, decimal_places=2)]
Temperature = Annotated[Decimal, Field(ge=-50, le=60, max_digits=5, decimal_places=2)]
Humidity = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class CheckpointResponse(BaseModel):
    number: int
    opens_on: date
    closes_on: date
    state: CheckpointState

    @classmethod
    def of(cls, checkpoint: Checkpoint) -> "CheckpointResponse":
        return cls(**vars(checkpoint))


class JournalResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    species: str
    planted_on: date | None
    created_at: datetime
    checkpoints: list[CheckpointResponse]

    @classmethod
    def of(cls, view: JournalView) -> "JournalResponse":
        journal = view.journal
        return cls(
            id=journal.id,
            order_id=journal.order_id,
            species=journal.species,
            planted_on=journal.planted_on,
            created_at=journal.created_at,
            checkpoints=[CheckpointResponse.of(checkpoint) for checkpoint in view.checkpoints],
        )


class JournalListResponse(BaseModel):
    items: list[JournalResponse]
    next_cursor: str | None

    @classmethod
    def of(cls, page: JournalPage) -> "JournalListResponse":
        return cls(
            items=[JournalResponse.of(view) for view in page.items], next_cursor=page.next_cursor
        )


class PlantingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planted_on: date


class SubmitUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    lighting: Lighting
    last_watered_on: date | None = None
    watering_method: WateringMethod = WateringMethod.UNKNOWN
    pot_diameter_class: PotDiameterClass = PotDiameterClass.UNKNOWN
    drainage_holes: Answer = Answer.UNKNOWN
    standing_water_over_1h: Answer = Answer.UNKNOWN
    dry_depth: DryDepth = DryDepth.UNKNOWN
    pot_weight_relative: PotWeightRelative = PotWeightRelative.UNKNOWN
    pot_weight_after_watering_g: PotWeightGrams | None = None
    pot_weight_now_g: PotWeightGrams | None = None
    repotted_or_moved: YesNo | None = None
    sensor_reading: SensorReading | None = None
    sensor_unit: SensorUnit | None = None
    air_temp_c: Temperature | None = None
    air_rh_pct: Humidity | None = None
    air_measured_at: AwareDatetime | None = None
    air_temp_min_c: Temperature | None = None
    air_temp_max_c: Temperature | None = None
    air_rh_min_pct: Humidity | None = None
    air_rh_max_pct: Humidity | None = None

    @field_validator("last_watered_on")
    @classmethod
    def not_in_the_future(cls, value: date | None) -> date | None:
        if value is not None and value > datetime.now(UTC).date():
            raise ValueError("last_watered_on must not be in the future")
        return value

    @model_validator(mode="after")
    def consistent_ranges_and_sensor(self) -> "SubmitUpdateRequest":
        if (self.sensor_reading is None) != (self.sensor_unit is None):
            raise ValueError("sensor_reading and sensor_unit must be given together")
        if self.sensor_unit is SensorUnit.VWC_PCT and self.sensor_reading > 100:
            raise ValueError("sensor_reading must be at most 100 when sensor_unit is vwc_pct")
        for low, high in (
            (self.air_temp_min_c, self.air_temp_max_c),
            (self.air_rh_min_pct, self.air_rh_max_pct),
        ):
            if low is not None and high is not None and low > high:
                raise ValueError("a minimum must not exceed its maximum")
        return self

    def to_new_update(self, journal_id: uuid.UUID) -> NewUpdate:
        return NewUpdate(journal_id=journal_id, **self.model_dump())


class RecommendedIntervalBounds(BaseModel):
    lower_days: float | None
    upper_days: float | None
    text: str | None


class AirContextResponse(BaseModel):
    T: float | None
    RH: float | None
    VPD_kPa: float | None


class CalibrationResponse(BaseModel):
    n_observations: int
    reset_reason: str | None


class WateringResponse(BaseModel):
    """The watering assessment computed for the diagnosis attempt (see docs/watering.md)."""

    status: WateringStatus
    reliability: WateringReliability
    depletion_D: float | None
    D_source: DepletionSource
    target_p: float
    p_source: PSource
    I_rec_days: float | None
    I_rec_bounds: RecommendedIntervalBounds | None
    I_fact_days: int | None
    ratio_r: float | None
    Ks: float | None
    flags: list[WateringFlag]
    air: AirContextResponse
    calibration: CalibrationResponse
    assumptions: list[str]


class DiagnosisResponse(BaseModel):
    status: DiagnosisStatus
    model_effort: str | None
    has_dominant_candidate: bool | None
    clarifying_question: str | None
    clarifying_answer: str | None
    roots_photo_requested: bool
    recommendation: str | None
    escalation_offered: bool
    escalation_declined: bool
    watering: WateringResponse | None

    @classmethod
    def of(cls, diagnosis: DiagnosisSummary) -> "DiagnosisResponse":
        return cls(**vars(diagnosis))


class CandidateResponse(BaseModel):
    rank: int
    cause: str
    rationale: str


class DiagnosisDetailResponse(DiagnosisResponse):
    update_id: uuid.UUID
    candidates: list[CandidateResponse]

    @classmethod
    def of_diagnosis(cls, diagnosis: Diagnosis) -> "DiagnosisDetailResponse":
        return cls(
            update_id=diagnosis.update_id,
            candidates=[CandidateResponse(**vars(c)) for c in diagnosis.candidates],
            **vars(diagnosis.summary),
        )


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


class EscalationResponse(BaseModel):
    id: uuid.UUID
    status: EscalationStatus
    answer_text: str | None
    answered_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, escalation: EscalationSummary) -> "EscalationResponse":
        return cls(**vars(escalation))


class EscalationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_question: str | None = None


class EscalationDetailResponse(EscalationResponse):
    update_id: uuid.UUID
    user_question: str | None

    @classmethod
    def of_escalation(cls, escalation: Escalation) -> "EscalationDetailResponse":
        return cls(
            id=escalation.id,
            status=escalation.status,
            answer_text=escalation.answer_text,
            answered_at=escalation.answered_at,
            created_at=escalation.created_at,
            update_id=escalation.update_id,
            user_question=escalation.user_question,
        )


class UpdateResponse(BaseModel):
    id: uuid.UUID
    journal_id: uuid.UUID
    created_at: datetime
    lighting: Lighting
    last_watered_on: date | None
    watering_method: WateringMethod
    pot_diameter_class: PotDiameterClass
    drainage_holes: Answer
    standing_water_over_1h: Answer
    dry_depth: DryDepth
    pot_weight_relative: PotWeightRelative
    pot_weight_after_watering_g: Decimal | None
    pot_weight_now_g: Decimal | None
    repotted_or_moved: YesNo | None
    sensor_reading: Decimal | None
    sensor_unit: SensorUnit | None
    air_temp_c: Decimal | None
    air_rh_pct: Decimal | None
    air_measured_at: datetime | None
    air_temp_min_c: Decimal | None
    air_temp_max_c: Decimal | None
    air_rh_min_pct: Decimal | None
    air_rh_max_pct: Decimal | None
    diagnosis: DiagnosisResponse | None
    escalation: EscalationResponse | None

    @classmethod
    def of(cls, item: FeedItem) -> "UpdateResponse":
        return cls(
            **vars(item.update),
            diagnosis=None if item.diagnosis is None else DiagnosisResponse.of(item.diagnosis),
            escalation=None if item.escalation is None else EscalationResponse.of(item.escalation),
        )


class FeedResponse(BaseModel):
    items: list[UpdateResponse]
    next_cursor: str | None

    @classmethod
    def of(cls, page: FeedPage) -> "FeedResponse":
        return cls(
            items=[UpdateResponse.of(item) for item in page.items], next_cursor=page.next_cursor
        )
