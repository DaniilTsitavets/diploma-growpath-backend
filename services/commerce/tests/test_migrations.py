from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.models import Base

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


@pytest.fixture
def migrated_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("COMMERCE_DATABASE_URL", url)
    return url


@pytest.fixture
def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def test_migrations_produce_the_schema_the_models_declare(migrated_url, alembic_config):
    command.upgrade(alembic_config, "head")

    engine = create_engine(migrated_url)
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []


def test_downgrade_removes_every_table(migrated_url, alembic_config):
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    tables = set(inspect(create_engine(migrated_url)).get_table_names())
    assert tables == {"alembic_version"}
