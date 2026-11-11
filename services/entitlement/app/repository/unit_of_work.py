from datetime import timedelta
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.repository.placed_orders import SqlAlchemyPlacedOrderRepository
from app.repository.reservations import SqlAlchemyAiReservations, SqlAlchemyExpertReservations


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session], lock_timeout: timedelta) -> None:
        self._session_factory = session_factory
        self._lock_timeout = lock_timeout

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.placed_orders = SqlAlchemyPlacedOrderRepository(self._session)
        self.ai_reservations = SqlAlchemyAiReservations(self._session, self._lock_timeout)
        self.expert_reservations = SqlAlchemyExpertReservations(self._session, self._lock_timeout)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._session.close()

    def commit(self) -> None:
        self._session.commit()
