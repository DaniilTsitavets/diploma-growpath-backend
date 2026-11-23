import uuid

from sqlalchemy import Select, select, true, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models import DiagnosisModel, EscalationModel, JournalModel, UpdateModel
from app.service.model import (
    Answer,
    DiagnosisStatus,
    DiagnosisSummary,
    DryDepth,
    EscalationStatus,
    EscalationSummary,
    FeedCursor,
    FeedItem,
    Lighting,
    NewUpdate,
    PotDiameterClass,
    PotWeightRelative,
    SensorUnit,
    UpdateRecord,
    WateringMethod,
    YesNo,
)

UPDATE_COLUMNS = tuple(UpdateModel.__table__.c)


def to_update(row: Row) -> UpdateRecord:
    fields = row._mapping
    repotted_or_moved = fields["repotted_or_moved"]
    sensor_unit = fields["sensor_unit"]
    return UpdateRecord(
        id=fields["id"],
        journal_id=fields["journal_id"],
        lighting=Lighting(fields["lighting"]),
        last_watered_on=fields["last_watered_on"],
        watering_method=WateringMethod(fields["watering_method"]),
        pot_diameter_class=PotDiameterClass(fields["pot_diameter_class"]),
        drainage_holes=Answer(fields["drainage_holes"]),
        standing_water_over_1h=Answer(fields["standing_water_over_1h"]),
        dry_depth=DryDepth(fields["dry_depth"]),
        pot_weight_relative=PotWeightRelative(fields["pot_weight_relative"]),
        pot_weight_after_watering_g=fields["pot_weight_after_watering_g"],
        pot_weight_now_g=fields["pot_weight_now_g"],
        repotted_or_moved=None if repotted_or_moved is None else YesNo(repotted_or_moved),
        sensor_reading=fields["sensor_reading"],
        sensor_unit=None if sensor_unit is None else SensorUnit(sensor_unit),
        air_temp_c=fields["air_temp_c"],
        air_rh_pct=fields["air_rh_pct"],
        air_measured_at=fields["air_measured_at"],
        air_temp_min_c=fields["air_temp_min_c"],
        air_temp_max_c=fields["air_temp_max_c"],
        air_rh_min_pct=fields["air_rh_min_pct"],
        air_rh_max_pct=fields["air_rh_max_pct"],
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
                lighting=update.lighting,
                last_watered_on=update.last_watered_on,
                watering_method=update.watering_method,
                pot_diameter_class=update.pot_diameter_class,
                drainage_holes=update.drainage_holes,
                standing_water_over_1h=update.standing_water_over_1h,
                dry_depth=update.dry_depth,
                pot_weight_relative=update.pot_weight_relative,
                pot_weight_after_watering_g=update.pot_weight_after_watering_g,
                pot_weight_now_g=update.pot_weight_now_g,
                repotted_or_moved=update.repotted_or_moved,
                sensor_reading=update.sensor_reading,
                sensor_unit=update.sensor_unit,
                air_temp_c=update.air_temp_c,
                air_rh_pct=update.air_rh_pct,
                air_measured_at=update.air_measured_at,
                air_temp_min_c=update.air_temp_min_c,
                air_temp_max_c=update.air_temp_max_c,
                air_rh_min_pct=update.air_rh_min_pct,
                air_rh_max_pct=update.air_rh_max_pct,
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
