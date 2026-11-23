import base64
import binascii
import re
from collections.abc import Callable

from app.service.errors import InvalidCursor, ItemSlugNotFound
from app.service.model import CatalogEntry, CatalogPage
from app.service.ports import UnitOfWork

SLUG_PATTERN = re.compile(r"[a-z0-9-]{1,64}")


def encode_cursor(slug: str) -> str:
    return base64.urlsafe_b64encode(slug.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    try:
        slug = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        raise InvalidCursor(cursor) from None
    if SLUG_PATTERN.fullmatch(slug) is None:
        raise InvalidCursor(cursor)
    return slug


class CatalogService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        *,
        default_page_size: int,
        max_page_size: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size

    def list_items(
        self, label: str | None = None, limit: int | None = None, cursor: str | None = None
    ) -> CatalogPage:
        """One page of active items by slug; a `limit` above the maximum is clamped."""
        size = min(limit or self._default_page_size, self._max_page_size)
        after_slug = None if cursor is None else decode_cursor(cursor)
        with self._unit_of_work() as uow:
            entries = uow.catalog.list_active(label, limit=size + 1, after_slug=after_slug)
        has_more = len(entries) > size
        items = entries[:size]
        return CatalogPage(items, encode_cursor(items[-1].slug) if has_more else None)

    def get_item(self, slug: str) -> CatalogEntry:
        """The active item with this slug; a malformed slug cannot match, so it is not found too."""
        if SLUG_PATTERN.fullmatch(slug) is None:
            raise ItemSlugNotFound(slug)
        with self._unit_of_work() as uow:
            entry = uow.catalog.find_active_by_slug(slug)
        if entry is None:
            raise ItemSlugNotFound(slug)
        return entry
