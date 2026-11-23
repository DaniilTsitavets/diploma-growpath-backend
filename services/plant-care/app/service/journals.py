import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.service.checkpoints import Checkpoint, checkpoints_for
from app.service.cursor import decode_cursor, encode_cursor
from app.service.errors import JournalNotFound
from app.service.model import FeedCursor, Journal
from app.service.ports import UnitOfWork


@dataclass(frozen=True)
class JournalView:
    journal: Journal
    checkpoints: list[Checkpoint]


@dataclass(frozen=True)
class JournalPage:
    items: list[JournalView]
    next_cursor: str | None


def utc_today() -> date:
    return datetime.now(UTC).date()


class JournalService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        default_page_size: int,
        max_page_size: int,
        today: Callable[[], date] = utc_today,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size
        self._today = today

    def page_for_user(
        self, user_id: uuid.UUID, limit: int | None, cursor: str | None
    ) -> JournalPage:
        page_size = min(limit or self._default_page_size, self._max_page_size)
        before = decode_cursor(cursor) if cursor is not None else None
        with self._unit_of_work() as uow:
            journals = uow.journals.page_for_user(user_id, page_size + 1, before)
        page = journals[:page_size]
        next_cursor = None
        if len(journals) > page_size:
            next_cursor = encode_cursor(FeedCursor(page[-1].created_at, page[-1].id))
        return JournalPage([self._view(journal) for journal in page], next_cursor)

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
