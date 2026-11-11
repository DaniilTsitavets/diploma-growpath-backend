import uuid
from datetime import datetime, timedelta

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.orm import Session

from app.models import DiagnosisCandidateModel, DiagnosisModel, EscalationModel, UpdateModel
from app.repository.escalations import escalation_select, to_escalation
from app.service.model import DiagnosisStatus, Escalation, HistoryEntry

REQUESTED = "requested"


class SqlAlchemyEscalationDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_due(self, grace: timedelta, ttl: timedelta, limit: int) -> list[Escalation]:
        return self._claim(
            EscalationModel.created_at < func.now() - grace,
            EscalationModel.created_at > func.now() - ttl,
            limit=limit,
        )

    def claim_expired(self, ttl: timedelta, limit: int) -> list[Escalation]:
        return self._claim(EscalationModel.created_at <= func.now() - ttl, limit=limit)

    def cancel_requested(self, escalation_id: uuid.UUID) -> bool:
        result = self._session.execute(
            update(EscalationModel)
            .where(EscalationModel.id == escalation_id, EscalationModel.status == REQUESTED)
            .values(status="cancelled")
            .returning(EscalationModel.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    def history(self, journal_id: uuid.UUID, before: datetime, limit: int) -> list[HistoryEntry]:
        rows = self._session.execute(
            select(UpdateModel.created_at, DiagnosisModel.status, DiagnosisCandidateModel.cause)
            .select_from(UpdateModel)
            .outerjoin(DiagnosisModel, DiagnosisModel.update_id == UpdateModel.id)
            .outerjoin(
                DiagnosisCandidateModel,
                and_(
                    DiagnosisCandidateModel.update_id == UpdateModel.id,
                    DiagnosisCandidateModel.rank == 1,
                ),
            )
            .where(UpdateModel.journal_id == journal_id, UpdateModel.created_at < before)
            .order_by(UpdateModel.created_at.desc(), UpdateModel.id.desc())
            .limit(limit)
        )
        return [
            HistoryEntry(
                created_at=created_at,
                diagnosis_status=None if status is None else DiagnosisStatus(status),
                top_cause=cause,
            )
            for created_at, status, cause in rows
        ]

    def _claim(self, *conditions, limit: int) -> list[Escalation]:
        statement: Select = (
            escalation_select()
            .where(EscalationModel.status == REQUESTED, *conditions)
            .order_by(EscalationModel.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True, of=EscalationModel)
        )
        return [to_escalation(row) for row in self._session.execute(statement)]
