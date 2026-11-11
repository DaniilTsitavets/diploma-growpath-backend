import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.service.ai_quota import AiQuota
from app.service.cursor import decode_cursor, encode_cursor
from app.service.errors import AiQuotaUnavailable, JournalNotFound, UpdateNotFound
from app.service.model import FeedCursor, FeedItem, NewUpdate
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Submitted:
    item: FeedItem
    created: bool


@dataclass(frozen=True)
class FeedPage:
    items: list[FeedItem]
    next_cursor: str | None


class UpdateService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        ai_quota: AiQuota,
        default_page_size: int,
        max_page_size: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._ai_quota = ai_quota
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size

    def submit(self, user_id: uuid.UUID, update: NewUpdate) -> Submitted:
        """Store the update and its pending diagnosis; a retried id returns the stored update."""
        replayed = self._find_stored(user_id, update)
        if replayed is not None:
            return Submitted(replayed, created=False)

        reservation_id = uuid.uuid4()
        reserved = self._reserve(reservation_id, user_id)
        try:
            item, created = self._store(update, reservation_id)
        except Exception:
            self._release(reservation_id, user_id, reserved)
            raise
        if not created:
            self._release(reservation_id, user_id, reserved)
        else:
            logger.info(
                "update stored",
                extra={"update_id": str(update.id), "journal_id": str(update.journal_id)},
            )
        return Submitted(item, created)

    def feed(
        self, user_id: uuid.UUID, journal_id: uuid.UUID, limit: int | None, cursor: str | None
    ) -> FeedPage:
        page_size = min(limit or self._default_page_size, self._max_page_size)
        before = decode_cursor(cursor) if cursor is not None else None
        with self._unit_of_work() as uow:
            if uow.journals.get_owned(journal_id, user_id) is None:
                raise JournalNotFound
            items = uow.updates.feed(journal_id, page_size + 1, before)
        if len(items) <= page_size:
            return FeedPage(items, None)
        page = items[:page_size]
        last = page[-1].update
        return FeedPage(page, encode_cursor(FeedCursor(last.created_at, last.id)))

    def _find_stored(self, user_id: uuid.UUID, update: NewUpdate) -> FeedItem | None:
        with self._unit_of_work() as uow:
            if uow.journals.get_owned(update.journal_id, user_id) is None:
                raise JournalNotFound
            return self._same_journal(uow.updates.item(update.id), update)

    def _store(self, update: NewUpdate, reservation_id: uuid.UUID) -> tuple[FeedItem, bool]:
        with self._unit_of_work() as uow:
            created = uow.updates.add(update)
            if created:
                uow.diagnoses.add_pending(update.id, reservation_id)
                uow.commit()
            item = self._same_journal(uow.updates.item(update.id), update)
        if item is None:
            raise UpdateNotFound
        return item, created

    @staticmethod
    def _same_journal(item: FeedItem | None, update: NewUpdate) -> FeedItem | None:
        """An id that already belongs to another journal must look like a missing update."""
        if item is None:
            return None
        if item.update.journal_id != update.journal_id:
            raise UpdateNotFound
        return item

    def _reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """False when entitlement is unavailable: the update is accepted without a reservation."""
        try:
            self._ai_quota.reserve(reservation_id, user_id)
        except AiQuotaUnavailable:
            logger.warning(
                "ai quota unavailable, the diagnosis starts pending",
                extra={"reservation_id": str(reservation_id)},
            )
            return False
        return True

    def _release(self, reservation_id: uuid.UUID, user_id: uuid.UUID, reserved: bool) -> None:
        if not reserved:
            return
        try:
            self._ai_quota.release(reservation_id, user_id)
        except AiQuotaUnavailable:
            logger.warning(
                "ai quota reservation could not be released, it will expire",
                extra={"reservation_id": str(reservation_id)},
            )
