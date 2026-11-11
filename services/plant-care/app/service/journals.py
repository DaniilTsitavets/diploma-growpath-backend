import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.service.checkpoints import Checkpoint, checkpoints_for
from app.service.errors import JournalNotFound
from app.service.model import Journal
from app.service.ports import UnitOfWork


@dataclass(frozen=True)
class JournalView:
    journal: Journal
    checkpoints: list[Checkpoint]


def utc_today() -> date:
    return datetime.now(UTC).date()


class JournalService:
    def __init__(
        self, unit_of_work: Callable[[], UnitOfWork], today: Callable[[], date] = utc_today
    ) -> None:
        self._unit_of_work = unit_of_work
        self._today = today

    def list_for_user(self, user_id: uuid.UUID) -> list[JournalView]:
        with self._unit_of_work() as uow:
            journals = uow.journals.list_for_user(user_id)
        return [self._view(journal) for journal in journals]

    def get(self, journal_id: uuid.UUID, user_id: uuid.UUID) -> JournalView:
        with self._unit_of_work() as uow:
            journal = uow.journals.get_owned(journal_id, user_id)
        if journal is None:
            raise JournalNotFound
        return self._view(journal)

    def plant(self, journal_id: uuid.UUID, user_id: uuid.UUID, planted_on: date) -> JournalView:
        with self._unit_of_work() as uow:
            journal = uow.journals.set_planted_on(journal_id, user_id, planted_on)
            uow.commit()
        if journal is None:
            raise JournalNotFound
        return self._view(journal)

    def _view(self, journal: Journal) -> JournalView:
        return JournalView(journal, checkpoints_for(journal.planted_on, self._today()))
