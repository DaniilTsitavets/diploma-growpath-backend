import uuid

from sqlalchemy import Select, select, true, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models import DiagnosisModel, EscalationModel, JournalModel, UpdateModel
from app.service.model import (
    DiagnosisStatus,
    DiagnosisSummary,
    Drainage,
    EscalationStatus,
    EscalationSummary,
    FeedCursor,
    FeedItem,
    FingerTest,
    Lighting,
    NewUpdate,
    PotWeight,
    UpdateRecord,
)

UPDATE_COLUMNS = tuple(UpdateModel.__table__.c)


def to_update(row: Row) -> UpdateRecord:
    fields = row._mapping
    return UpdateRecord(
        id=fields["id"],
        journal_id=fields["journal_id"],
        last_watered_on=fields["last_watered_on"],
        watering_interval_days=fields["watering_interval_days"],
        drainage=Drainage(fields["drainage"]),
        lighting=Lighting(fields["lighting"]),
        finger_test=FingerTest(fields["finger_test"]),
        pot_weight=PotWeight(fields["pot_weight"]),
        sensor_reading=fields["sensor_reading"],
        created_at=fields["created_at"],
    )


class SqlAlchemyUpdateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, update: NewUpdate) -> bool:
        result = self._session.execute(
            insert(UpdateModel)
            .values(
                id=update.id,
                journal_id=update.journal_id,
                last_watered_on=update.last_watered_on,
                watering_interval_days=update.watering_interval_days,
                drainage=update.drainage.value,
                lighting=update.lighting.value,
                finger_test=update.finger_test.value,
                pot_weight=update.pot_weight.value,
                sensor_reading=update.sensor_reading,
            )
            .on_conflict_do_nothing(index_elements=[UpdateModel.id])
            .returning(UpdateModel.id)
        )
        return result.scalar_one_or_none() is not None

    def item(self, update_id: uuid.UUID) -> FeedItem | None:
        row = self._session.execute(
            _feed_statement().where(UpdateModel.id == update_id)
        ).one_or_none()
        return None if row is None else _to_feed_item(row)

    def get_owned(self, update_id: uuid.UUID, user_id: uuid.UUID) -> UpdateRecord | None:
        row = self._session.execute(
            select(*UPDATE_COLUMNS)
            .join(JournalModel, JournalModel.id == UpdateModel.journal_id)
            .where(UpdateModel.id == update_id, JournalModel.user_id == user_id)
        ).one_or_none()
        return None if row is None else to_update(row)

    def feed(self, journal_id: uuid.UUID, limit: int, before: FeedCursor | None) -> list[FeedItem]:
        statement = (
            _feed_statement()
            .where(UpdateModel.journal_id == journal_id)
            .order_by(UpdateModel.created_at.desc(), UpdateModel.id.desc())
            .limit(limit)
        )
        if before is not None:
            statement = statement.where(
                tuple_(UpdateModel.created_at, UpdateModel.id)
                < tuple_(before.created_at, before.id)
            )
        return [_to_feed_item(row) for row in self._session.execute(statement)]


def _feed_statement() -> Select:
    """Updates with their diagnosis and, through LATERAL, the latest escalation of each."""
    latest_escalation = (
        select(
            EscalationModel.id.label("escalation_id"),
            EscalationModel.status.label("escalation_status"),
            EscalationModel.answer_text,
            EscalationModel.answered_at,
            EscalationModel.created_at.label("escalation_created_at"),
        )
        .where(EscalationModel.update_id == UpdateModel.id)
        .order_by(EscalationModel.created_at.desc())
        .limit(1)
        .lateral("latest_escalation")
    )
    return (
        select(
            *UPDATE_COLUMNS,
            DiagnosisModel.status.label("diagnosis_status"),
            DiagnosisModel.model_effort,
            DiagnosisModel.has_dominant_candidate,
            DiagnosisModel.clarifying_question,
            DiagnosisModel.clarifying_answer,
            DiagnosisModel.roots_photo_requested,
            DiagnosisModel.recommendation,
            DiagnosisModel.escalation_offered,
            DiagnosisModel.escalation_declined,
            latest_escalation,
        )
        .select_from(UpdateModel)
        .outerjoin(DiagnosisModel, DiagnosisModel.update_id == UpdateModel.id)
        .outerjoin(latest_escalation, true())
    )


def _to_feed_item(row: Row) -> FeedItem:
    fields = row._mapping
    diagnosis = None
    if fields["diagnosis_status"] is not None:
        diagnosis = DiagnosisSummary(
            status=DiagnosisStatus(fields["diagnosis_status"]),
            model_effort=fields["model_effort"],
            has_dominant_candidate=fields["has_dominant_candidate"],
            clarifying_question=fields["clarifying_question"],
            clarifying_answer=fields["clarifying_answer"],
            roots_photo_requested=fields["roots_photo_requested"],
            recommendation=fields["recommendation"],
            escalation_offered=fields["escalation_offered"],
            escalation_declined=fields["escalation_declined"],
        )
    escalation = None
    if fields["escalation_id"] is not None:
        escalation = EscalationSummary(
            id=fields["escalation_id"],
            status=EscalationStatus(fields["escalation_status"]),
            answer_text=fields["answer_text"],
            answered_at=fields["answered_at"],
            created_at=fields["escalation_created_at"],
        )
    return FeedItem(update=to_update(row), diagnosis=diagnosis, escalation=escalation)
