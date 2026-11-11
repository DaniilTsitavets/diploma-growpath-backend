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
