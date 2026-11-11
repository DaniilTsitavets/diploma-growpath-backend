import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import CatalogItem, CatalogItemLabel
from app.repository.catalog import SqlAlchemyCatalogRepository
from tests.db.factories import make_item, make_sensor


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


@pytest.fixture
def catalog(session: Session) -> SqlAlchemyCatalogRepository:
    session.add_all(
        [
            make_item(
                name="Mint Pack",
                plant_species="Mint",
                labels=[CatalogItemLabel(label="rare"), CatalogItemLabel(label="beginner")],
            ),
            make_item(name="Basil Kit", labels=[CatalogItemLabel(label="beginner")]),
            make_sensor(),
            make_item(name="Retired Pack", is_active=False),
        ]
    )
    session.flush()
    return SqlAlchemyCatalogRepository(session)


def test_list_returns_active_items_by_name_with_sorted_labels(
    catalog: SqlAlchemyCatalogRepository,
):
    entries = catalog.list_active()

    assert [(e.name, e.labels) for e in entries] == [
        ("Basil Kit", ("beginner",)),
        ("Mint Pack", ("beginner", "rare")),
        ("Soil Sensor", ()),
    ]


def test_list_can_be_filtered_by_label(catalog: SqlAlchemyCatalogRepository):
    assert [e.name for e in catalog.list_active("rare")] == ["Mint Pack"]
    assert [e.name for e in catalog.list_active("beginner")] == ["Basil Kit", "Mint Pack"]
    assert catalog.list_active("unknown") == []


def test_list_of_an_empty_catalog_is_empty(session: Session):
    assert SqlAlchemyCatalogRepository(session).list_active() == []
