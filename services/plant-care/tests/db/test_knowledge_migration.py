import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from tests.db.conftest import alembic_config

NEW_TABLES = ("kb_documents", "kb_sections", "chat_turns", "chat_turn_sources")
OLD_TABLES = {
    "journals",
    "updates",
    "update_photos",
    "diagnoses",
    "diagnosis_candidates",
    "escalations",
}
CONSTRAINTS = {
    "pk_kb_documents",
    "uq_kb_documents_slug",
    "ck_kb_documents_slug",
    "ck_kb_documents_source_url",
    "ck_kb_documents_sha",
    "ck_kb_documents_lengths",
    "pk_kb_sections",
    "fk_kb_sections_document_id_kb_documents",
    "uq_kb_sections_document_position",
    "uq_kb_sections_document_anchor",
    "ck_kb_sections_position",
    "ck_kb_sections_lengths",
    "pk_chat_turns",
    "fk_chat_turns_journal_id_journals",
    "uq_chat_turns_ai_reservation_id",
    "ck_chat_turns_outcome",
    "ck_chat_turns_model_effort",
    "ck_chat_turns_lengths",
    "pk_chat_turn_sources",
    "fk_chat_turn_sources_turn_id_chat_turns",
    "ck_chat_turn_sources_rank",
    "ck_chat_turn_sources_lengths",
}


@pytest.fixture
def migrated(blank_database: URL, migrate):
    engine = create_engine(blank_database)
    with engine.connect() as connection:
        migrate(connection, "head")
        yield connection
    engine.dispose()


def test_the_chat_and_knowledge_constraints_have_the_names_of_the_design(migrated):
    tables = ", ".join(f"'{name}'" for name in NEW_TABLES)

    names = migrated.execute(
        text(
            "SELECT conname FROM pg_constraint"
            f" WHERE contype IN ('p', 'c', 'u', 'f') AND conrelid::regclass::text IN ({tables})"
        )
    ).scalars()

    assert set(names) == CONSTRAINTS


def test_the_search_index_is_a_gin_index_on_the_search_vector(migrated):
    indexdef = migrated.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_kb_sections_search_vector'")
    ).scalar_one()

    assert "USING gin (search_vector)" in indexdef


def test_the_history_index_is_ordered_newest_first(migrated):
    indexdef = migrated.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chat_turns_journal_created'")
    ).scalar_one()

    assert "(journal_id, created_at DESC, id DESC)" in indexdef


def test_the_search_vector_is_a_stored_generated_column_weighting_the_heading_above_the_body(
    migrated,
):
    generated, expression = migrated.execute(
        text(
            "SELECT a.attgenerated, pg_get_expr(d.adbin, d.adrelid)"
            " FROM pg_attribute a JOIN pg_attrdef d"
            " ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
            " WHERE a.attrelid = 'kb_sections'::regclass AND a.attname = 'search_vector'"
        )
    ).one()

    assert generated == "s"
    assert "setweight(to_tsvector('english'::regconfig, heading), 'A'" in expression
    assert "setweight(to_tsvector('english'::regconfig, body), 'B'" in expression


def test_the_citation_has_no_foreign_key_to_the_section(migrated):
    referenced = migrated.execute(
        text(
            "SELECT confrelid::regclass::text FROM pg_constraint"
            " WHERE conrelid = 'chat_turn_sources'::regclass AND contype = 'f'"
        )
    ).scalars()

    assert set(referenced) == {"chat_turns"}


def test_the_creation_time_defaults_to_now(migrated):
    defaults = migrated.execute(
        text(
            "SELECT table_name, column_default FROM information_schema.columns"
            " WHERE column_name = 'created_at' AND table_name IN ('kb_documents', 'chat_turns')"
        )
    ).all()

    assert dict(defaults) == {"kb_documents": "now()", "chat_turns": "now()"}


def test_downgrade_removes_only_the_new_tables_and_upgrade_restores_them(migrated, migrate):
    command.downgrade(alembic_config(migrated), "0001")
    migrated.commit()

    assert set(inspect(migrated).get_table_names()) == OLD_TABLES | {"alembic_version"}

    migrate(migrated, "head")

    assert set(inspect(migrated).get_table_names()) == OLD_TABLES | set(NEW_TABLES) | {
        "alembic_version"
    }
