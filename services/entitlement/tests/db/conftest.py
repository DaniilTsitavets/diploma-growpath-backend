import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.repository.unit_of_work import SqlAlchemyUnitOfWork

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"
ADMIN_URL = os.environ.get(
    "ENTITLEMENT_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:55432/postgres"
)
SESSION_OPTIONS = {"options": "-c timezone=UTC"}


@contextmanager
def temporary_database() -> Iterator[URL]:
    """Create an empty database on the test server and drop it afterwards."""
    name = f"entitlement_test_{uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    quoted = admin.dialect.identifier_preparer.quote(name)
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {quoted}"))
    try:
        yield make_url(ADMIN_URL).set(database=name)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {quoted} WITH (FORCE)"))
        admin.dispose()


def alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


def run_alembic(connection: Connection, revision: str) -> None:
    """Run Alembic up to (or down to) `revision` on `connection` and commit."""
    config = alembic_config(connection)
    if revision == "base":
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)
    connection.commit()


@pytest.fixture
def migrate() -> Callable[[Connection, str], None]:
    return run_alembic


@pytest.fixture
def blank_database() -> Iterator[URL]:
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="session")
def database_url() -> Iterator[URL]:
    """A database whose schema was built by `alembic upgrade head`."""
    with temporary_database() as url:
        engine = create_engine(url)
        with engine.connect() as connection:
            run_alembic(connection, "head")
        engine.dispose()
        yield url


@pytest.fixture(scope="session")
def engine(database_url: URL) -> Iterator[Engine]:
    engine = create_engine(database_url, connect_args=SESSION_OPTIONS)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def empty_tables_after_test(engine: Engine) -> Iterator[None]:
    """Tests of races, handlers and the API commit their rows; later tests expect empty tables."""
    yield
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE placed_orders, ai_quota_reservations, expert_quota_reservations")
        )


@pytest.fixture
def unit_of_work(engine: Engine) -> Callable[..., SqlAlchemyUnitOfWork]:
    """Builds units of work over the shared engine; pass `lock_timeout` to shorten the wait."""
    return partial(SqlAlchemyUnitOfWork, sessionmaker(engine), lock_timeout=timedelta(seconds=2))


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back at the end of the test."""
    with engine.connect() as connection:
        outer = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        outer.rollback()
