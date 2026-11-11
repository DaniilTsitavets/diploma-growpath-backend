from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CatalogItem, CatalogItemLabel
from app.seed import CATALOG, seed_catalog


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_seed_inserts_the_demo_catalog_that_satisfies_the_schema(session: Session):
    seed_catalog(session)

    assert count(session, CatalogItem) == len(CATALOG)
    assert count(session, CatalogItemLabel) == sum(len(item.labels) for item in CATALOG)


def test_seeding_twice_changes_nothing(session: Session):
    seed_catalog(session)
    seed_catalog(session)

    assert count(session, CatalogItem) == len(CATALOG)
    assert count(session, CatalogItemLabel) == sum(len(item.labels) for item in CATALOG)


def test_seeding_does_not_overwrite_an_edited_item(session: Session):
    seed_catalog(session)
    kit = session.scalars(select(CatalogItem).where(CatalogItem.name == "Beginner's Kit")).one()
    kit.price_cents = 9900
    session.flush()

    seed_catalog(session)
    session.expire_all()

    assert kit.price_cents == 9900


def test_only_the_beginners_kit_carries_the_first_purchase_discount(session: Session):
    seed_catalog(session)

    discounted = session.scalars(
        select(CatalogItem).where(CatalogItem.first_purchase_discount_pct.is_not(None))
    ).all()

    assert [(item.name, item.first_purchase_discount_pct) for item in discounted] == [
        ("Beginner's Kit", 20)
    ]


def test_catalog_has_a_separately_sold_sensor(session: Session):
    seed_catalog(session)

    sensors = session.scalars(select(CatalogItem).where(CatalogItem.item_type == "sensor")).all()

    assert [(s.plant_species, s.seed_count) for s in sensors] == [(None, None)]
