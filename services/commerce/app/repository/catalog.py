import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogItem
from app.service.model import Item


class SqlAlchemyCatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active(self, item_id: uuid.UUID) -> Item | None:
        row = self._session.execute(
            select(
                CatalogItem.id,
                CatalogItem.item_type,
                CatalogItem.name,
                CatalogItem.plant_species,
                CatalogItem.seed_count,
                CatalogItem.price_cents,
                CatalogItem.first_purchase_discount_pct,
                CatalogItem.is_active,
            ).where(CatalogItem.id == item_id, CatalogItem.is_active)
        ).one_or_none()
        return None if row is None else Item(**row._mapping)
