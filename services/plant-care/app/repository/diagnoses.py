import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import DiagnosisModel
from app.service.model import DiagnosisStatus


class SqlAlchemyDiagnosisRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_pending(self, update_id: uuid.UUID, ai_reservation_id: uuid.UUID) -> bool:
        result = self._session.execute(
            insert(DiagnosisModel)
            .values(
                update_id=update_id,
                ai_reservation_id=ai_reservation_id,
                status=DiagnosisStatus.PENDING.value,
            )
            .on_conflict_do_nothing()
            .returning(DiagnosisModel.update_id)
        )
        return result.scalar_one_or_none() is not None
