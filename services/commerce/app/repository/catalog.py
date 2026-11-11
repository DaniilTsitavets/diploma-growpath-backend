import uuid
from collections import defaultdict

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import CatalogItem, CatalogItemLabel
from app.service.model import CatalogEntry, Item

ITEM_COLUMNS = (
    CatalogItem.id,
    CatalogItem.item_type,
    CatalogItem.name,
    CatalogItem.plant_species,
    CatalogItem.seed_count,
    CatalogItem.price_cents,
    CatalogItem.first_purchase_discount_pct,
    CatalogItem.is_active,
)


class SqlAlchemyCatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, item_id: uuid.UUID) -> Item | None:
        return self._find(CatalogItem.id == item_id)

    def get_active(self, item_id: uuid.UUID) -> Item | None:
        return self._find(CatalogItem.id == item_id, CatalogItem.is_active)

    def _find(self, *conditions) -> Item | None:
        row = self._session.execute(select(*ITEM_COLUMNS).where(*conditions)).one_or_none()
        return None if row is None else Item(**row._mapping)

    def list_active(self, label: str | None = None) -> list[CatalogEntry]:
        query = select(*ITEM_COLUMNS).where(CatalogItem.is_active).order_by(CatalogItem.name)
        if label is not None:
            query = query.where(
                exists().where(
                    CatalogItemLabel.item_id == CatalogItem.id, CatalogItemLabel.label == label
                )
            )
        rows = self._session.execute(query).all()
        labels = self._labels_of([row.id for row in rows])
        return [CatalogEntry(**row._mapping, labels=labels[row.id]) for row in rows]

    def _labels_of(self, item_ids: list[uuid.UUID]) -> defaultdict[uuid.UUID, tuple[str, ...]]:
        found: defaultdict[uuid.UUID, list[str]] = defaultdict(list)
        if item_ids:
            rows = self._session.execute(
                select(CatalogItemLabel.item_id, CatalogItemLabel.label)
                .where(CatalogItemLabel.item_id.in_(item_ids))
                .order_by(CatalogItemLabel.label)
            )
            for item_id, label in rows:
                found[item_id].append(label)
        return defaultdict(tuple, {item_id: tuple(names) for item_id, names in found.items()})
