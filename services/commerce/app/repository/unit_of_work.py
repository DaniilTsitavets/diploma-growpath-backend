from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.repository.catalog import SqlAlchemyCatalogRepository
from app.repository.orders import SqlAlchemyOrderRepository


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.orders = SqlAlchemyOrderRepository(self._session)
        self.catalog = SqlAlchemyCatalogRepository(self._session)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._session.close()

    def commit(self) -> None:
        self._session.commit()
