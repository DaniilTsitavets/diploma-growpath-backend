import uuid
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import JournalModel
from app.service.model import Journal, NewJournal

COLUMNS = tuple(JournalModel.__table__.c)


class SqlAlchemyJournalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, journal: NewJournal) -> bool:
        result = self._session.execute(
            insert(JournalModel)
            .values(
                id=journal.id,
                user_id=journal.user_id,
                order_id=journal.order_id,
                species=journal.species,
            )
            .on_conflict_do_nothing(index_elements=[JournalModel.order_id])
            .returning(JournalModel.id)
        )
        return result.scalar_one_or_none() is not None

    def get_owned(self, journal_id: uuid.UUID, user_id: uuid.UUID) -> Journal | None:
        row = self._session.execute(
            select(*COLUMNS).where(JournalModel.id == journal_id, JournalModel.user_id == user_id)
        ).one_or_none()
        return None if row is None else Journal(**row._mapping)

    def list_for_user(self, user_id: uuid.UUID) -> list[Journal]:
        rows = self._session.execute(
            select(*COLUMNS)
            .where(JournalModel.user_id == user_id)
            .order_by(JournalModel.created_at.desc(), JournalModel.id.desc())
        )
        return [Journal(**row._mapping) for row in rows]

    def set_planted_on(
        self, journal_id: uuid.UUID, user_id: uuid.UUID, planted_on: date
    ) -> Journal | None:
        row = self._session.execute(
            update(JournalModel)
            .where(JournalModel.id == journal_id, JournalModel.user_id == user_id)
            .values(planted_on=planted_on)
            .returning(*COLUMNS)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return None if row is None else Journal(**row._mapping)
