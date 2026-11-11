import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import CatalogItem, CatalogItemLabel
from tests.db.factories import make_item, make_sensor, violated


def test_item_is_active_by_default_and_loads_its_labels(session: Session):
    session.add(make_item(labels=[CatalogItemLabel(label="rare"), CatalogItemLabel(label="herbs")]))
    session.flush()
    session.expire_all()

    stored = session.scalars(select(CatalogItem)).one()

    assert stored.is_active is True
    assert {label.label for label in stored.labels} == {"rare", "herbs"}


def test_sensor_has_neither_species_nor_seed_count(session: Session):
    session.add(make_sensor())
    session.flush()

    stored = session.scalars(select(CatalogItem)).one()

    assert (stored.item_type, stored.plant_species, stored.seed_count) == ("sensor", None, None)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"seed_count": None}, "ck_catalog_items_kind"),
        ({"plant_species": None}, "ck_catalog_items_kind"),
        ({"item_type": "sensor", "seed_count": None}, "ck_catalog_items_kind"),
        ({"item_type": "sensor", "plant_species": None}, "ck_catalog_items_kind"),
        (
            {"item_type": "gift", "plant_species": None, "seed_count": None},
            "ck_catalog_items_item_type",
        ),
        ({"seed_count": 0}, "ck_catalog_items_seed_count_positive"),
        ({"price_cents": -1}, "ck_catalog_items_price_non_negative"),
        ({"first_purchase_discount_pct": 0}, "ck_catalog_items_discount_pct"),
        ({"first_purchase_discount_pct": 100}, "ck_catalog_items_discount_pct"),
        ({"plant_species": "   "}, "ck_catalog_items_plant_species"),
        ({"plant_species": "x" * 81}, "ck_catalog_items_plant_species"),
        ({"name": ""}, "ck_catalog_items_name_length"),
        ({"name": "x" * 121}, "ck_catalog_items_name_length"),
    ],
    ids=[
        "pack without seed count",
        "pack without species",
        "sensor with species",
        "sensor with seed count",
        "unknown item type",
        "pack with zero seeds",
        "negative price",
        "discount percent 0",
        "discount percent 100",
        "blank species",
        "species of 81 characters",
        "empty name",
        "name of 121 characters",
    ],
)
def test_catalog_item_rejects_invalid_values(session: Session, overrides: dict, constraint: str):
    with violated(session) as violation:
        session.add(make_item(**overrides))

    assert violation.constraint == constraint


@pytest.mark.parametrize("pct", [1, 99])
def test_discount_percent_accepts_the_bounds(session: Session, pct: int):
    session.add(make_item(first_purchase_discount_pct=pct))

    session.flush()


def test_item_names_are_unique(session: Session, item: CatalogItem):
    with violated(session) as violation:
        session.add(make_item(name=item.name))

    assert violation.constraint == "uq_catalog_items_name"


@pytest.mark.parametrize("label", ["", "x" * 41], ids=["empty label", "label of 41 characters"])
def test_label_length_is_limited(session: Session, item: CatalogItem, label: str):
    with violated(session) as violation:
        session.add(CatalogItemLabel(item_id=item.id, label=label))

    assert violation.constraint == "ck_catalog_item_labels_label_length"


def test_item_cannot_repeat_a_label(session: Session, item: CatalogItem):
    session.add(CatalogItemLabel(item_id=item.id, label="rare"))
    session.flush()

    with violated(session) as violation:
        session.add(CatalogItemLabel(item_id=item.id, label="rare"))

    assert violation.constraint == "pk_catalog_item_labels"


def test_label_requires_an_existing_item(session: Session):
    with violated(session) as violation:
        session.add(CatalogItemLabel(item_id=uuid.uuid4(), label="rare"))

    assert violation.constraint == "fk_catalog_item_labels_item_id_catalog_items"


def test_item_with_labels_cannot_be_deleted(session: Session, item: CatalogItem):
    session.add(CatalogItemLabel(item_id=item.id, label="rare"))
    session.flush()

    with violated(session) as violation:
        session.execute(delete(CatalogItem).where(CatalogItem.id == item.id))

    assert violation.constraint == "fk_catalog_item_labels_item_id_catalog_items"
