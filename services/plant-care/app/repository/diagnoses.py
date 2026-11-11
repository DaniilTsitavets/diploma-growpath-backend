import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import DiagnosisCandidateModel, DiagnosisModel
from app.service.model import (
    CandidateRecord,
    Diagnosis,
    DiagnosisResult,
    DiagnosisStatus,
    DiagnosisSummary,
    ModelEffort,
)

PENDING = DiagnosisStatus.PENDING.value
ACTIVE = (PENDING, DiagnosisStatus.DEGRADED.value)


class SqlAlchemyDiagnosisRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_pending(self, update_id: uuid.UUID, ai_reservation_id: uuid.UUID) -> bool:
        result = self._session.execute(
            insert(DiagnosisModel)
            .values(update_id=update_id, ai_reservation_id=ai_reservation_id, status=PENDING)
            .on_conflict_do_nothing()
            .returning(DiagnosisModel.update_id)
        )
        return result.scalar_one_or_none() is not None

    def get(self, update_id: uuid.UUID) -> Diagnosis | None:
        row = self._session.scalars(
            select(DiagnosisModel)
            .where(DiagnosisModel.update_id == update_id)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            return None
        candidates = self._session.scalars(
            select(DiagnosisCandidateModel)
            .where(DiagnosisCandidateModel.update_id == update_id)
            .order_by(DiagnosisCandidateModel.rank)
        )
        return Diagnosis(
            update_id=row.update_id,
            ai_reservation_id=row.ai_reservation_id,
            summary=DiagnosisSummary(
                status=DiagnosisStatus(row.status),
                model_effort=row.model_effort,
                has_dominant_candidate=row.has_dominant_candidate,
                clarifying_question=row.clarifying_question,
                clarifying_answer=row.clarifying_answer,
                roots_photo_requested=row.roots_photo_requested,
                recommendation=row.recommendation,
                escalation_offered=row.escalation_offered,
                escalation_declined=row.escalation_declined,
            ),
            candidates=[CandidateRecord(c.rank, c.cause, c.rationale) for c in candidates],
        )

    def claim_first_attempt(
        self, update_id: uuid.UUID, reservation_id: uuid.UUID, effort: ModelEffort
    ) -> bool:
        return self._guarded_update(
            DiagnosisModel.update_id == update_id,
            DiagnosisModel.ai_reservation_id == reservation_id,
            DiagnosisModel.status == PENDING,
            DiagnosisModel.model_effort.is_(None),
            values={"model_effort": effort.value},
        )

    def claim_retry(
        self,
        update_id: uuid.UUID,
        old_reservation_id: uuid.UUID,
        new_reservation_id: uuid.UUID,
        effort: ModelEffort,
    ) -> bool:
        return self._guarded_update(
            DiagnosisModel.update_id == update_id,
            DiagnosisModel.ai_reservation_id == old_reservation_id,
            DiagnosisModel.status.in_(ACTIVE),
            values={
                "ai_reservation_id": new_reservation_id,
                "status": PENDING,
                "model_effort": effort.value,
                "has_dominant_candidate": None,
                "recommendation": None,
            },
        )

    def answer_clarification(self, update_id: uuid.UUID, answer: str) -> bool:
        return self._guarded_update(
            DiagnosisModel.update_id == update_id,
            DiagnosisModel.status == DiagnosisStatus.AWAITING_CLARIFICATION.value,
            DiagnosisModel.clarifying_answer.is_(None),
            values={"clarifying_answer": answer, "status": PENDING},
        )

    def record_result(
        self, update_id: uuid.UUID, reservation_id: uuid.UUID, result: DiagnosisResult
    ) -> bool:
        values: dict[str, object] = {
            "status": result.status.value,
            "has_dominant_candidate": result.has_dominant_candidate,
            "recommendation": result.recommendation,
            "escalation_offered": result.escalation_offered,
        }
        if result.clarifying_question is not None:
            values["clarifying_question"] = result.clarifying_question
            values["roots_photo_requested"] = result.roots_photo_requested
        stored = self._guarded_update(
            DiagnosisModel.update_id == update_id,
            DiagnosisModel.ai_reservation_id == reservation_id,
            DiagnosisModel.status == PENDING,
            values=values,
        )
        if stored:
            self._replace_candidates(update_id, result)
        return stored

    def mark_degraded(
        self, update_id: uuid.UUID, reservation_id: uuid.UUID, recommendation: str
    ) -> bool:
        return self._guarded_update(
            DiagnosisModel.update_id == update_id,
            DiagnosisModel.ai_reservation_id == reservation_id,
            DiagnosisModel.status == PENDING,
            values={"status": DiagnosisStatus.DEGRADED.value, "recommendation": recommendation},
        )

    def decline_escalation(self, update_id: uuid.UUID) -> bool:
        return self._guarded_update(
            DiagnosisModel.update_id == update_id,
            DiagnosisModel.escalation_offered.is_(True),
            DiagnosisModel.escalation_declined.is_(False),
            values={"escalation_declined": True},
        )

    def _guarded_update(self, *conditions, values: dict[str, object]) -> bool:
        result = self._session.execute(
            update(DiagnosisModel)
            .where(*conditions)
            .values(**values)
            .returning(DiagnosisModel.update_id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    def _replace_candidates(self, update_id: uuid.UUID, result: DiagnosisResult) -> None:
        self._session.execute(
            delete(DiagnosisCandidateModel).where(DiagnosisCandidateModel.update_id == update_id)
        )
        if result.candidates:
            self._session.execute(
                insert(DiagnosisCandidateModel),
                [
                    {
                        "update_id": update_id,
                        "rank": rank,
                        "cause": candidate.cause,
                        "rationale": candidate.rationale,
                    }
                    for rank, candidate in enumerate(result.candidates, start=1)
                ],
            )
