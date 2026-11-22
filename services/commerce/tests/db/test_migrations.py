import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from app.models import Base

TABLES = "('catalog_items', 'catalog_item_labels', 'orders')"

CONSTRAINTS = {
    "pk_catalog_items",
    "uq_catalog_items_name",
    "ck_catalog_items_item_type",
    "ck_catalog_items_name_length",
    "ck_catalog_items_plant_species",
    "ck_catalog_items_price_non_negative",
    "ck_catalog_items_discount_pct",
    "ck_catalog_items_kind",
    "ck_catalog_items_seed_count_positive",
    "uq_catalog_items_slug",
    "ck_catalog_items_slug",
    "ck_catalog_items_plant_slug",
    "pk_catalog_item_labels",
    "fk_catalog_item_labels_item_id_catalog_items",
    "ck_catalog_item_labels_label_length",
    "pk_orders",
    "fk_orders_item_id_catalog_items",
    "uq_orders_user_id_idempotency_key",
    "ck_orders_idempotency_key_length",
    "ck_orders_list_price_non_negative",
    "ck_orders_amounts",
}
STANDALONE_INDEXES = {"uq_orders_one_discount_per_user", "ix_orders_unpublished"}


@pytest.fixture
def migrated(blank_database: URL, migrate):
    engine = create_engine(blank_database)
    with engine.connect() as connection:
        migrate(connection, "head")
        yield connection
    engine.dispose()


def test_migrations_produce_the_schema_the_models_declare(migrated):
    differences = compare_metadata(MigrationContext.configure(migrated), Base.metadata)

    assert differences == []


def test_constraints_and_indexes_have_the_names_of_the_design(migrated):
    constraints = migrated.execute(
        text(f"SELECT conname FROM pg_constraint WHERE conrelid::regclass::text IN {TABLES}")
    ).scalars()
    indexes = migrated.execute(
        text(f"SELECT indexname FROM pg_indexes WHERE tablename IN {TABLES}")
    ).scalars()

    assert set(constraints) == CONSTRAINTS
    assert set(indexes) - CONSTRAINTS == STANDALONE_INDEXES


def test_downgrade_removes_every_table(migrated, migrate):
    migrate(migrated, "base")

    assert set(inspect(migrated).get_table_names()) == {"alembic_version"}


LEGACY_ITEMS = (
    ("pack", "Beginner's Kit", "Basil", 5),
    ("pack", "Chili Pepper Pack", "Chili pepper", 8),
    ("pack", "Tea  &  Herbs -- Pack!", "  --Lemon Balm (Melissa)!-- ", 3),
    ("sensor", "Soil Moisture Sensor", None, None),
)


@pytest.fixture
def legacy_catalog(blank_database: URL, migrate):
    """The schema of migration 0001 with items that have no slug yet."""
    engine = create_engine(blank_database)
    with engine.connect() as connection:
        migrate(connection, "0001")
        for item_type, name, species, seed_count in LEGACY_ITEMS:
            connection.execute(
                text(
                    "INSERT INTO catalog_items (id, item_type, name, plant_species, seed_count,"
                    " price_cents) VALUES (gen_random_uuid(), :item_type, :name, :species,"
                    " :seed_count, 1000)"
                ),
                {
                    "item_type": item_type,
                    "name": name,
                    "species": species,
                    "seed_count": seed_count,
                },
            )
        connection.commit()
        yield connection
    engine.dispose()


def test_upgrade_backfills_slugs_from_names_and_species(legacy_catalog, migrate):
    migrate(legacy_catalog, "head")

    rows = legacy_catalog.execute(
        text("SELECT name, slug, plant_slug FROM catalog_items ORDER BY name")
    ).all()

    assert [tuple(row) for row in rows] == [
        ("Beginner's Kit", "beginners-kit", "basil"),
        ("Chili Pepper Pack", "chili-pepper-pack", "chili-pepper"),
        ("Soil Moisture Sensor", "soil-moisture-sensor", None),
        ("Tea  &  Herbs -- Pack!", "tea-herbs-pack", "lemon-balm-melissa"),
    ]


def test_upgrade_makes_slug_required(legacy_catalog, migrate):
    migrate(legacy_catalog, "head")

    columns = {
        column["name"]: column for column in inspect(legacy_catalog).get_columns("catalog_items")
    }

    assert columns["slug"]["nullable"] is False
    assert columns["plant_slug"]["nullable"] is True


def test_downgrade_to_0001_drops_the_slug_columns_and_constraints(
    legacy_catalog, migrate, downgrade
):
    migrate(legacy_catalog, "head")

    downgrade(legacy_catalog, "0001")

    inspector = inspect(legacy_catalog)
    assert {column["name"] for column in inspector.get_columns("catalog_items")}.isdisjoint(
        {"slug", "plant_slug"}
    )
    remaining = legacy_catalog.execute(
        text("SELECT conname FROM pg_constraint WHERE conrelid = 'catalog_items'::regclass")
    ).scalars()
    assert {name for name in remaining if "slug" in name} == set()
    assert legacy_catalog.execute(text("SELECT count(*) FROM catalog_items")).scalar() == len(
        LEGACY_ITEMS
    )
