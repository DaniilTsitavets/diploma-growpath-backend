import re
from collections.abc import Callable

from app.service.errors import ItemSlugNotFound
from app.service.model import CatalogEntry
from app.service.ports import UnitOfWork

SLUG_PATTERN = re.compile(r"[a-z0-9-]{1,64}")


class CatalogService:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def list_items(self, label: str | None = None) -> list[CatalogEntry]:
        with self._unit_of_work() as uow:
            return uow.catalog.list_active(label)

    def get_item(self, slug: str) -> CatalogEntry:
        """The active item with this slug; a malformed slug cannot match, so it is not found too."""
        if SLUG_PATTERN.fullmatch(slug) is None:
            raise ItemSlugNotFound(slug)
        with self._unit_of_work() as uow:
            entry = uow.catalog.find_active_by_slug(slug)
        if entry is None:
            raise ItemSlugNotFound(slug)
        return entry
