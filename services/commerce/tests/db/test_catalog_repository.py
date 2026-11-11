import uuid

from sqlalchemy.orm import Session

from app.models import CatalogItem
from app.repository.catalog import SqlAlchemyCatalogRepository
from tests.db.factories import make_item


def test_active_item_is_returned_with_its_catalog_fields(session: Session):
    stored = make_item(first_purchase_discount_pct=20)
    session.add(stored)
    session.flush()

    item = SqlAlchemyCatalogRepository(session).get_active(stored.id)

    assert (item.id, item.name, item.plant_species, item.seed_count) == (
        stored.id,
        "Basil Kit",
        "Basil",
        5,
    )
    assert (item.price_cents, item.first_purchase_discount_pct, item.item_type) == (
        2500,
        20,
        "pack",
    )


def test_inactive_item_is_not_returned(session: Session, item: CatalogItem):
    item.is_active = False
    session.flush()

    assert SqlAlchemyCatalogRepository(session).get_active(item.id) is None


def test_unknown_item_is_not_returned(session: Session):
    assert SqlAlchemyCatalogRepository(session).get_active(uuid.uuid4()) is None
