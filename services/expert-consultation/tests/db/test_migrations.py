import re

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from app.models import Base

CONSTRAINTS = {
    "pk_tickets",
    "uq_tickets_escalation_id",
    "ck_tickets_context",
    "ck_tickets_state",
    "ck_tickets_times",
    "ck_tickets_event_published",
    "pk_answers",
    "fk_answers_ticket_id_tickets",
    "ck_answers_body",
}
PARTIAL_INDEXES = {
    "uq_tickets_active_update": "(update_id) WHERE status = 'open'",
    "ix_tickets_open_expires": "(expires_at, id) WHERE status = 'open'",
    "ix_tickets_unpublished": (
        "(id) WHERE status <> 'open' AND terminal_event_published_at IS NULL"
    ),
}


def skip_partial_indexes(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic cannot compare predicates; pg_indexes is checked below."""
    return not (type_ == "index" and name in PARTIAL_INDEXES)


def normalized(definition: str) -> str:
    """The index definition without the casts and parentheses PostgreSQL adds."""
    without_casts = definition.replace("::text", "")
    return re.sub(r"\s+", " ", without_casts.replace("(", "").replace(")", "")).strip()


@pytest.fixture
def migrated(blank_database: URL, migrate):
    engine = create_engine(blank_database)
    with engine.connect() as connection:
        migrate(connection, "head")
        yield connection
    engine.dispose()


def test_migrations_produce_the_schema_the_models_declare(migrated):
    context = MigrationContext.configure(migrated, opts={"include_object": skip_partial_indexes})

    assert compare_metadata(context, Base.metadata) == []


def test_constraints_have_the_names_of_the_design(migrated):
    constraints = migrated.execute(
        text(
            "SELECT conname FROM pg_constraint"
            " WHERE contype IN ('p', 'c', 'u', 'f') AND conrelid::regclass::text IN"
            " ('tickets', 'answers')"
        )
    ).scalars()

    assert set(constraints) == CONSTRAINTS


def test_the_tables_have_no_other_indexes_than_the_design_names(migrated):
    indexes = migrated.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename IN ('tickets', 'answers')")
    ).scalars()

    assert set(indexes) == {
        "pk_tickets",
        "uq_tickets_escalation_id",
        "pk_answers",
        *PARTIAL_INDEXES,
    }


@pytest.mark.parametrize(("index", "definition"), PARTIAL_INDEXES.items())
def test_partial_indexes_match_the_design(migrated, index: str, definition: str):
    indexdef = migrated.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": index}
    ).scalar_one()

    assert normalized(definition) in normalized(indexdef)


def test_the_active_update_index_is_unique_and_the_others_are_not(migrated):
    unique = dict(
        migrated.execute(
            text(
                "SELECT indexname, indexdef LIKE 'CREATE UNIQUE INDEX%' FROM pg_indexes"
                " WHERE indexname IN ('uq_tickets_active_update', 'ix_tickets_open_expires',"
                " 'ix_tickets_unpublished')"
            )
        ).all()
    )

    assert unique == {
        "uq_tickets_active_update": True,
        "ix_tickets_open_expires": False,
        "ix_tickets_unpublished": False,
    }


def test_a_ticket_starts_open_and_stamped_by_the_database_clock(migrated):
    defaults = dict(
        migrated.execute(
            text(
                "SELECT column_name, column_default FROM information_schema.columns"
                " WHERE table_name = 'tickets' AND column_name IN"
                " ('status', 'created_at', 'expires_at', 'terminal_event_published_at')"
            )
        ).all()
    )

    assert defaults == {
        "status": "'open'::text",
        "created_at": "now()",
        "expires_at": None,
        "terminal_event_published_at": None,
    }


def test_downgrade_removes_every_table(migrated, migrate):
    migrate(migrated, "base")

    assert set(inspect(migrated).get_table_names()) == {"alembic_version"}
