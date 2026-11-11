from collections.abc import Callable

from app.service.model import CatalogEntry
from app.service.ports import UnitOfWork


class CatalogService:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def list_items(self, label: str | None = None) -> list[CatalogEntry]:
        with self._unit_of_work() as uow:
            return uow.catalog.list_active(label)
