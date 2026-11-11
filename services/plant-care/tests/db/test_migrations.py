import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from app.models import Base

TABLES = (
    "('journals', 'updates', 'update_photos', 'diagnoses', 'diagnosis_candidates', 'escalations')"
)

CONSTRAINTS = {
    "pk_journals",
    "uq_journals_order_id",
    "ck_journals_species",
    "pk_updates",
    "fk_updates_journal_id_journals",
    "ck_updates_watering_interval_days",
    "ck_updates_drainage",
    "ck_updates_lighting",
    "ck_updates_finger_test",
    "ck_updates_pot_weight",
    "ck_updates_sensor_reading",
    "pk_update_photos",
    "fk_update_photos_update_id_updates",
    "ck_update_photos_kind",
    "ck_update_photos_storage_key",
    "pk_diagnoses",
    "fk_diagnoses_update_id_updates",
    "uq_diagnoses_ai_reservation_id",
    "ck_diagnoses_status",
    "ck_diagnoses_model_effort",
    "ck_diagnoses_lengths",
    "ck_diagnoses_answer_needs_question",
    "ck_diagnoses_awaiting",
    "ck_diagnoses_roots_photo",
    "ck_diagnoses_model_effort_set",
    "ck_diagnoses_no_recommendation_yet",
    "ck_diagnoses_finished",
    "ck_diagnoses_escalation_offered",
    "ck_diagnoses_escalation_declined",
    "pk_diagnosis_candidates",
    "fk_diagnosis_candidates_update_id_diagnoses",
    "ck_diagnosis_candidates_rank",
    "ck_diagnosis_candidates_lengths",
    "pk_escalations",
    "fk_escalations_update_id_updates",
    "uq_escalations_ticket_id",
    "ck_escalations_status",
    "ck_escalations_lengths",
    "ck_escalations_ticket_open",
    "ck_escalations_answered",
}
ORDERED_OR_PARTIAL_INDEXES = {
    "ix_journals_user_created": "(user_id, created_at DESC)",
    "ix_updates_journal_created": "(journal_id, created_at DESC, id DESC)",
    "ix_escalations_update_created": "(update_id, created_at DESC)",
    "uq_escalations_active_update": "(update_id) WHERE (status <> 'cancelled'::text)",
    "ix_escalations_requested": "(created_at) WHERE (status = 'requested'::text)",
}


def skip_ordered_and_partial_indexes(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic cannot compare sort order and predicates; pg_indexes is checked below."""
    return not (type_ == "index" and name in ORDERED_OR_PARTIAL_INDEXES)


@pytest.fixture
def migrated(blank_database: URL, migrate):
    engine = create_engine(blank_database)
    with engine.connect() as connection:
        migrate(connection, "head")
        yield connection
    engine.dispose()


def test_migrations_produce_the_schema_the_models_declare(migrated):
    context = MigrationContext.configure(
        migrated, opts={"include_object": skip_ordered_and_partial_indexes}
    )

    assert compare_metadata(context, Base.metadata) == []


def test_constraints_have_the_names_of_the_design(migrated):
    constraints = migrated.execute(
        text(
            "SELECT conname FROM pg_constraint"
            f" WHERE contype IN ('p', 'c', 'u', 'f') AND conrelid::regclass::text IN {TABLES}"
        )
    ).scalars()

    assert set(constraints) == CONSTRAINTS


@pytest.mark.parametrize(("index", "definition"), ORDERED_OR_PARTIAL_INDEXES.items())
def test_ordered_and_partial_indexes_match_the_design(migrated, index: str, definition: str):
    indexdef = migrated.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": index}
    ).scalar_one()

    assert definition in indexdef


def test_the_active_escalation_index_is_unique(migrated):
    indexdef = migrated.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_escalations_active_update'")
    ).scalar_one()

    assert indexdef.startswith("CREATE UNIQUE INDEX")


def test_flags_default_to_false_and_escalations_to_requested(migrated):
    defaults = dict(
        migrated.execute(
            text(
                "SELECT table_name || '.' || column_name, column_default"
                " FROM information_schema.columns"
                " WHERE (table_name = 'diagnoses' AND column_name IN"
                " ('roots_photo_requested', 'escalation_offered', 'escalation_declined'))"
                " OR (table_name = 'escalations' AND column_name = 'status')"
            )
        ).all()
    )

    assert defaults == {
        "diagnoses.roots_photo_requested": "false",
        "diagnoses.escalation_offered": "false",
        "diagnoses.escalation_declined": "false",
        "escalations.status": "'requested'::text",
    }


def test_downgrade_removes_every_table(migrated, migrate):
    migrate(migrated, "base")

    assert set(inspect(migrated).get_table_names()) == {"alembic_version"}
