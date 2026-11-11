from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.repository.diagnoses import SqlAlchemyDiagnosisRepository
from app.repository.escalation_delivery import SqlAlchemyEscalationDeliveryRepository
from app.repository.escalations import SqlAlchemyEscalationRepository
from app.repository.journals import SqlAlchemyJournalRepository
from app.repository.photos import SqlAlchemyPhotoRepository
from app.repository.updates import SqlAlchemyUpdateRepository


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.journals = SqlAlchemyJournalRepository(self._session)
        self.updates = SqlAlchemyUpdateRepository(self._session)
        self.diagnoses = SqlAlchemyDiagnosisRepository(self._session)
        self.photos = SqlAlchemyPhotoRepository(self._session)
        self.escalations = SqlAlchemyEscalationRepository(self._session)
        self.escalation_delivery = SqlAlchemyEscalationDeliveryRepository(self._session)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._session.close()

    def commit(self) -> None:
        self._session.commit()
