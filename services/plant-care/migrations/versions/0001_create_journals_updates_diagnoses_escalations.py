"""create journals, updates, photos, diagnoses, candidates and escalations

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "journals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("species", sa.Text(), nullable=False),
        sa.Column("planted_on", sa.Date(), nullable=True),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journals")),
        sa.UniqueConstraint("order_id", name=op.f("uq_journals_order_id")),
        sa.CheckConstraint(
            "btrim(species) <> '' AND char_length(species) <= 100",
            name=op.f("ck_journals_species"),
        ),
    )
    op.create_index(
        op.f("ix_journals_user_created"), "journals", ["user_id", sa.text("created_at DESC")]
    )

    op.create_table(
        "updates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("last_watered_on", sa.Date(), nullable=True),
        sa.Column("watering_interval_days", sa.Integer(), nullable=True),
        sa.Column("drainage", sa.Text(), nullable=False),
        sa.Column("lighting", sa.Text(), nullable=False),
        sa.Column("finger_test", sa.Text(), nullable=False),
        sa.Column("pot_weight", sa.Text(), nullable=False),
        sa.Column("sensor_reading", sa.Numeric(10, 2), nullable=True),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_updates")),
        sa.ForeignKeyConstraint(
            ["journal_id"], ["journals.id"], name=op.f("fk_updates_journal_id_journals")
        ),
        sa.CheckConstraint(
            "watering_interval_days BETWEEN 1 AND 365",
            name=op.f("ck_updates_watering_interval_days"),
        ),
        sa.CheckConstraint(
            "drainage IN ('good', 'poor', 'none')", name=op.f("ck_updates_drainage")
        ),
        sa.CheckConstraint(
            "lighting IN ('low', 'medium', 'bright_indirect', 'direct_sun')",
            name=op.f("ck_updates_lighting"),
        ),
        sa.CheckConstraint(
            "finger_test IN ('dry', 'slightly_moist', 'wet')", name=op.f("ck_updates_finger_test")
        ),
        sa.CheckConstraint(
            "pot_weight IN ('lighter', 'as_expected', 'heavier')",
            name=op.f("ck_updates_pot_weight"),
        ),
        sa.CheckConstraint("sensor_reading >= 0", name=op.f("ck_updates_sensor_reading")),
    )
    op.create_index(
        op.f("ix_updates_journal_created"),
        "updates",
        ["journal_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "update_photos",
        sa.Column("update_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        created_at(),
        sa.PrimaryKeyConstraint("update_id", "kind", name=op.f("pk_update_photos")),
        sa.ForeignKeyConstraint(
            ["update_id"], ["updates.id"], name=op.f("fk_update_photos_update_id_updates")
        ),
        sa.CheckConstraint(
            "kind IN ('overview', 'soil', 'symptom_closeup', 'roots_drainage')",
            name=op.f("ck_update_photos_kind"),
        ),
        sa.CheckConstraint(
            "btrim(storage_key) <> '' AND char_length(storage_key) <= 512",
            name=op.f("ck_update_photos_storage_key"),
        ),
    )

    op.create_table(
        "diagnoses",
        sa.Column("update_id", sa.Uuid(), nullable=False),
        sa.Column("ai_reservation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model_effort", sa.Text(), nullable=True),
        sa.Column("has_dominant_candidate", sa.Boolean(), nullable=True),
        sa.Column("clarifying_question", sa.Text(), nullable=True),
        sa.Column("clarifying_answer", sa.Text(), nullable=True),
        sa.Column(
            "roots_photo_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column(
            "escalation_offered", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "escalation_declined", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        created_at(),
        sa.PrimaryKeyConstraint("update_id", name=op.f("pk_diagnoses")),
        sa.ForeignKeyConstraint(
            ["update_id"], ["updates.id"], name=op.f("fk_diagnoses_update_id_updates")
        ),
        sa.UniqueConstraint("ai_reservation_id", name=op.f("uq_diagnoses_ai_reservation_id")),
        sa.CheckConstraint(
            "status IN ('diagnosis_pending', 'awaiting_clarification', 'completed', 'degraded')",
            name=op.f("ck_diagnoses_status"),
        ),
        sa.CheckConstraint(
            "model_effort IN ('medium', 'high')", name=op.f("ck_diagnoses_model_effort")
        ),
        sa.CheckConstraint(
            "char_length(clarifying_question) <= 500 AND char_length(clarifying_answer) <= 1000"
            " AND char_length(recommendation) <= 8000",
            name=op.f("ck_diagnoses_lengths"),
        ),
        sa.CheckConstraint(
            "clarifying_answer IS NULL OR clarifying_question IS NOT NULL",
            name=op.f("ck_diagnoses_answer_needs_question"),
        ),
        sa.CheckConstraint(
            "status <> 'awaiting_clarification'"
            " OR (clarifying_question IS NOT NULL AND clarifying_answer IS NULL)",
            name=op.f("ck_diagnoses_awaiting"),
        ),
        sa.CheckConstraint(
            "NOT roots_photo_requested OR clarifying_question IS NOT NULL",
            name=op.f("ck_diagnoses_roots_photo"),
        ),
        sa.CheckConstraint(
            "status = 'diagnosis_pending' OR model_effort IS NOT NULL",
            name=op.f("ck_diagnoses_model_effort_set"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('diagnosis_pending', 'awaiting_clarification')"
            " OR recommendation IS NULL",
            name=op.f("ck_diagnoses_no_recommendation_yet"),
        ),
        sa.CheckConstraint(
            "(status <> 'completed' OR (recommendation IS NOT NULL"
            " AND has_dominant_candidate IS NOT NULL))"
            " AND (status <> 'degraded' OR recommendation IS NOT NULL)",
            name=op.f("ck_diagnoses_finished"),
        ),
        sa.CheckConstraint(
            "NOT escalation_offered"
            " OR (clarifying_answer IS NOT NULL AND has_dominant_candidate IS FALSE)",
            name=op.f("ck_diagnoses_escalation_offered"),
        ),
        sa.CheckConstraint(
            "NOT escalation_declined OR escalation_offered",
            name=op.f("ck_diagnoses_escalation_declined"),
        ),
    )

    op.create_table(
        "diagnosis_candidates",
        sa.Column("update_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("update_id", "rank", name=op.f("pk_diagnosis_candidates")),
        sa.ForeignKeyConstraint(
            ["update_id"],
            ["diagnoses.update_id"],
            name=op.f("fk_diagnosis_candidates_update_id_diagnoses"),
        ),
        sa.CheckConstraint("rank >= 1", name=op.f("ck_diagnosis_candidates_rank")),
        sa.CheckConstraint(
            "char_length(cause) <= 200 AND char_length(rationale) <= 1000",
            name=op.f("ck_diagnosis_candidates_lengths"),
        ),
    )

    op.create_table(
        "escalations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("update_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), server_default="requested", nullable=False),
        sa.Column("user_question", sa.Text(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escalations")),
        sa.ForeignKeyConstraint(
            ["update_id"], ["updates.id"], name=op.f("fk_escalations_update_id_updates")
        ),
        sa.UniqueConstraint("ticket_id", name=op.f("uq_escalations_ticket_id")),
        sa.CheckConstraint(
            "status IN ('requested', 'ticket_open', 'answered', 'cancelled')",
            name=op.f("ck_escalations_status"),
        ),
        sa.CheckConstraint(
            "char_length(user_question) <= 1000 AND char_length(answer_text) <= 5000",
            name=op.f("ck_escalations_lengths"),
        ),
        sa.CheckConstraint(
            "status <> 'ticket_open' OR ticket_id IS NOT NULL",
            name=op.f("ck_escalations_ticket_open"),
        ),
        sa.CheckConstraint(
            "(status = 'answered')"
            " = (answer_text IS NOT NULL AND answered_at IS NOT NULL AND ticket_id IS NOT NULL)",
            name=op.f("ck_escalations_answered"),
        ),
    )
    op.create_index(
        op.f("uq_escalations_active_update"),
        "escalations",
        ["update_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelled'"),
    )
    op.create_index(
        op.f("ix_escalations_update_created"),
        "escalations",
        ["update_id", sa.text("created_at DESC")],
    )
    op.create_index(
        op.f("ix_escalations_requested"),
        "escalations",
        ["created_at"],
        postgresql_where=sa.text("status = 'requested'"),
    )


def downgrade() -> None:
    op.drop_table("escalations")
    op.drop_table("diagnosis_candidates")
    op.drop_table("diagnoses")
    op.drop_table("update_photos")
    op.drop_table("updates")
    op.drop_table("journals")
