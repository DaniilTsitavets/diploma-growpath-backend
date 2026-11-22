import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from tests.db.conftest import alembic_config

CONSTRAINTS = {
    "pk_plant_profiles",
    "uq_plant_profiles_source_slug",
    "ck_plant_profiles_slug",
    "ck_plant_profiles_lengths",
    "ck_plant_profiles_text_lengths",
    "ck_plant_profiles_key_takeaways",
    "ck_plant_profiles_source_url",
    "ck_plant_profiles_license",
    "ck_plant_profiles_sha",
}
OPTIONAL = {
    "common_name",
    "scientific_name",
    "family",
    "difficulty",
    "summary",
    "quick_answer",
    "key_takeaways",
    "light",
    "sunlight_hours",
    "watering",
    "placement",
    "soil",
    "humidity",
    "temperature",
    "pruning",
    "toxicity",
}
REQUIRED = {
    "slug",
    "source_slug",
    "species",
    "source_name",
    "source_url",
    "license",
    "imported_on",
    "content_sha256",
    "created_at",
}


@pytest.fixture
def migrated(blank_database: URL, migrate):
    engine = create_engine(blank_database)
    with engine.connect() as connection:
        migrate(connection, "head")
        yield connection
    engine.dispose()


def test_the_constraints_have_the_names_of_the_design(migrated):
    names = migrated.execute(
        text(
            "SELECT conname FROM pg_constraint"
            " WHERE contype IN ('p', 'c', 'u', 'f') AND conrelid = 'plant_profiles'::regclass"
        )
    ).scalars()

    assert set(names) == CONSTRAINTS


def test_the_columns_are_nullable_only_where_the_design_says(migrated):
    columns = {column["name"]: column for column in inspect(migrated).get_columns("plant_profiles")}

    assert set(columns) == REQUIRED | OPTIONAL
    assert {name for name, column in columns.items() if not column["nullable"]} == REQUIRED
    assert str(columns["key_takeaways"]["type"]) == "JSONB"


def test_the_creation_time_defaults_to_now(migrated):
    default = migrated.execute(
        text(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'plant_profiles' AND column_name = 'created_at'"
        )
    ).scalar_one()

    assert default == "now()"


def test_downgrade_drops_the_table_and_upgrade_restores_it(migrated, migrate):
    command.downgrade(alembic_config(migrated), "0003")
    migrated.commit()

    assert "plant_profiles" not in inspect(migrated).get_table_names()
    assert "kb_documents" in inspect(migrated).get_table_names()

    migrate(migrated, "head")

    assert "plant_profiles" in inspect(migrated).get_table_names()
