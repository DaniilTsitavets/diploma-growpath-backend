import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.service.cursor import decode_cursor, encode_cursor
from app.service.model import QueueCursor, Ticket, TicketSummary
from app.service.photos import PhotoLink, PhotoStorage, photo_refs
from app.service.ports import UnitOfWork
from app.service.ticket_errors import TicketNotFound


@dataclass(frozen=True)
class QueuePage:
    items: list[TicketSummary]
    next_cursor: str | None


@dataclass(frozen=True)
class TicketView:
    ticket: Ticket
    photos: list[PhotoLink]


class ExpertTicketService:
    """What an expert sees: the queue of open tickets and one ticket with its photos."""

    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        storage: PhotoStorage,
        default_page_size: int,
        max_page_size: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size

    def queue(self, limit: int | None, cursor: str | None) -> QueuePage:
        """Soonest deadline first; pass `next_cursor` of a page as the cursor for the next one."""
        page_size = min(limit or self._default_page_size, self._max_page_size)
        after = decode_cursor(cursor) if cursor is not None else None
        with self._unit_of_work() as uow:
            items = uow.tickets.open_queue(page_size + 1, after)
        if len(items) <= page_size:
            return QueuePage(items, None)
        page = items[:page_size]
        return QueuePage(page, encode_cursor(QueueCursor(page[-1].expires_at, page[-1].id)))

    def view(self, ticket_id: uuid.UUID) -> TicketView:
        """The ticket with presigned URLs for its photos, issued at this read and never stored."""
        with self._unit_of_work() as uow:
            ticket = uow.tickets.get(ticket_id)
        if ticket is None:
            raise TicketNotFound
        photos = [
            PhotoLink(ref.kind, self._storage.download_url(ref.storage_key))
            for ref in photo_refs(ticket.context)
        ]
        return TicketView(ticket, photos)
