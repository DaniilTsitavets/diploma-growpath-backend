import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CatalogItem, CatalogItemLabel
from app.seed import CATALOG, LEGACY_PACK_NAMES, SEED_NAMESPACE, seed_catalog
from tests.db.factories import make_item, make_sensor

SENSOR_ID = uuid.uuid5(SEED_NAMESPACE, "Soil Moisture Sensor")


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def active(session: Session) -> list[CatalogItem]:
    return list(session.scalars(select(CatalogItem).where(CatalogItem.is_active)))


def with_legacy_items(session: Session) -> None:
    """The rows an earlier seed left behind; their slugs are what the 0002 backfill gives."""
    session.add_all(
        [
            make_item(name="Beginner's Kit", slug="beginners-kit", first_purchase_discount_pct=20),
            make_item(name="Mint Pack", slug="mint-pack", plant_species="Mint", plant_slug="mint"),
            make_item(name="Chili Pepper Pack", slug="chili-pepper-pack"),
            make_item(name="Lavender Pack", slug="lavender-pack", plant_slug="lavender"),
            make_sensor(id=SENSOR_ID, name="Soil Moisture Sensor", slug="soil-moisture-sensor"),
        ]
    )
    session.flush()


def test_seed_inserts_the_catalog_that_satisfies_the_schema(session: Session):
    seed_catalog(session)

    assert count(session, CatalogItem) == len(CATALOG) == 17
    assert count(session, CatalogItemLabel) == sum(len(item.labels) for item in CATALOG)


def test_a_fresh_catalog_has_fifteen_packs_and_two_sensors(session: Session):
    seed_catalog(session)

    by_type = [item.item_type for item in active(session)]

    assert (by_type.count("pack"), by_type.count("sensor")) == (15, 2)


def test_seeding_twice_changes_nothing(session: Session):
    with_legacy_items(session)
    seed_catalog(session)
    seed_catalog(session)

    assert count(session, CatalogItem) == len(CATALOG) + len(LEGACY_PACK_NAMES)
    assert count(session, CatalogItemLabel) == sum(len(item.labels) for item in CATALOG)
    assert len(active(session)) == len(CATALOG)


def test_seeding_does_not_overwrite_an_edited_item(session: Session):
    seed_catalog(session)
    pothos = session.scalars(select(CatalogItem).where(CatalogItem.slug == "golden-pothos")).one()
    pothos.price_cents = 9900
    session.flush()

    seed_catalog(session)
    session.expire_all()

    assert pothos.price_cents == 9900


def test_seeding_adds_a_missing_label_to_an_existing_item(session: Session):
    seed_catalog(session)
    cactus = session.scalars(select(CatalogItem).where(CatalogItem.slug == "cactus")).one()
    session.query(CatalogItemLabel).filter_by(item_id=cactus.id).delete()
    session.flush()

    seed_catalog(session)
    session.expire_all()

    assert [label.label for label in cactus.labels] == ["low-maintenance"]


def test_only_the_golden_pothos_pack_carries_the_first_purchase_discount(session: Session):
    seed_catalog(session)

    discounted = session.scalars(
        select(CatalogItem).where(CatalogItem.first_purchase_discount_pct.is_not(None))
    ).all()

    assert [(item.slug, item.first_purchase_discount_pct) for item in discounted] == [
        ("golden-pothos", 20)
    ]


def test_packs_use_their_plant_slug_as_item_slug_and_the_species_in_the_name(session: Session):
    seed_catalog(session)

    packs = session.scalars(select(CatalogItem).where(CatalogItem.item_type == "pack")).all()

    assert all(pack.slug == pack.plant_slug for pack in packs)
    assert all(pack.name == f"{pack.plant_species} Starter Pack" for pack in packs)


def test_the_catalog_has_two_separately_sold_sensors(session: Session):
    seed_catalog(session)

    sensors = session.scalars(select(CatalogItem).where(CatalogItem.item_type == "sensor")).all()

    assert sorted((s.slug, s.plant_slug, s.plant_species, s.seed_count) for s in sensors) == [
        ("soil-moisture-sensor", None, None, None),
        ("thermo-hygrometer", None, None, None),
    ]


def test_legacy_packs_are_deactivated_not_deleted(session: Session):
    with_legacy_items(session)

    seed_catalog(session)
    session.expire_all()

    legacy = session.scalars(
        select(CatalogItem).where(CatalogItem.name.in_(LEGACY_PACK_NAMES))
    ).all()
    assert len(legacy) == len(LEGACY_PACK_NAMES)
    assert [item.is_active for item in legacy] == [False] * len(LEGACY_PACK_NAMES)


def test_the_existing_sensor_stays_active_and_is_not_duplicated(session: Session):
    with_legacy_items(session)

    seed_catalog(session)

    sensors = session.scalars(
        select(CatalogItem).where(CatalogItem.slug == "soil-moisture-sensor")
    ).all()
    assert [(sensor.id, sensor.is_active) for sensor in sensors] == [(SENSOR_ID, True)]
    assert count(session, CatalogItem) == len(CATALOG) + len(LEGACY_PACK_NAMES)


def test_a_fresh_sensor_gets_the_id_the_earlier_seed_gave_it(session: Session):
    seed_catalog(session)

    sensor = session.scalars(
        select(CatalogItem).where(CatalogItem.slug == "soil-moisture-sensor")
    ).one()

    assert sensor.id == SENSOR_ID
