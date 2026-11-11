import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.service.checkpoints import Checkpoint, CheckpointState
from app.service.journals import JournalView
from app.service.model import (
    Diagnosis,
    DiagnosisStatus,
    DiagnosisSummary,
    Drainage,
    Escalation,
    EscalationStatus,
    EscalationSummary,
    FeedItem,
    FingerTest,
    Lighting,
    NewUpdate,
    PotWeight,
)
from app.service.updates import FeedPage

SensorReading = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


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


class PlantingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planted_on: date


class SubmitUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    last_watered_on: date | None = None
    watering_interval_days: Annotated[int, Field(ge=1, le=365)] | None = None
    drainage: Drainage
    lighting: Lighting
    finger_test: FingerTest
    pot_weight: PotWeight
    sensor_reading: SensorReading | None = None

    def to_new_update(self, journal_id: uuid.UUID) -> NewUpdate:
        return NewUpdate(journal_id=journal_id, **self.model_dump())


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
    last_watered_on: date | None
    watering_interval_days: int | None
    drainage: Drainage
    lighting: Lighting
    finger_test: FingerTest
    pot_weight: PotWeight
    sensor_reading: Decimal | None
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
