import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from app.models import Base

TABLES = "('placed_orders', 'ai_quota_reservations', 'expert_quota_reservations')"

CONSTRAINTS = {
    "pk_placed_orders",
    "ck_placed_orders_total_paid_non_negative",
    "pk_ai_quota_reservations",
    "ck_ai_quota_reservations_status",
    "ck_ai_quota_reservations_expiry",
    "pk_expert_quota_reservations",
    "ck_expert_quota_reservations_status",
    "ck_expert_quota_reservations_expiry",
}
STANDALONE_INDEXES = {
    "ix_placed_orders_user_placed",
    "ix_ai_quota_reservations_user_created",
    "ix_expert_quota_reservations_user_created",
}


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
        text(
            "SELECT conname FROM pg_constraint"
            f" WHERE contype IN ('p', 'c', 'u', 'f') AND conrelid::regclass::text IN {TABLES}"
        )
    ).scalars()
    indexes = migrated.execute(
        text(f"SELECT indexname FROM pg_indexes WHERE tablename IN {TABLES}")
    ).scalars()

    assert set(constraints) == CONSTRAINTS
    assert set(indexes) - CONSTRAINTS == STANDALONE_INDEXES


def test_tier_index_covers_the_amount_without_indexing_it(migrated):
    definition = migrated.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_placed_orders_user_placed'")
    ).scalar_one()

    assert "(user_id, placed_at) INCLUDE (total_paid_cents)" in definition


def test_reservations_start_as_reserved_and_are_stamped_by_the_database(migrated):
    defaults = dict(
        migrated.execute(
            text(
                "SELECT column_name, column_default FROM information_schema.columns"
                " WHERE table_name = 'ai_quota_reservations'"
                " AND column_name IN ('status', 'created_at')"
            )
        ).all()
    )

    assert defaults == {"status": "'reserved'::text", "created_at": "now()"}


def test_downgrade_removes_every_table(migrated, migrate):
    migrate(migrated, "base")

    assert set(inspect(migrated).get_table_names()) == {"alembic_version"}
