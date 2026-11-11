"""create tickets and answers

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("escalation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("update_id", sa.Uuid(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_event_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("escalation_id", name=op.f("uq_tickets_escalation_id")),
        sa.CheckConstraint(
            "jsonb_typeof(context) = 'object' AND octet_length(context::text) <= 131072",
            name=op.f("ck_tickets_context"),
        ),
        sa.CheckConstraint(
            "(status = 'open' AND answered_at IS NULL AND cancelled_at IS NULL)"
            " OR (status = 'answered' AND answered_at IS NOT NULL AND cancelled_at IS NULL)"
            " OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND answered_at IS NULL)",
            name=op.f("ck_tickets_state"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at"
            " AND (answered_at IS NULL"
            " OR (answered_at >= created_at AND answered_at <= expires_at))"
            " AND (cancelled_at IS NULL OR cancelled_at >= expires_at)",
            name=op.f("ck_tickets_times"),
        ),
        sa.CheckConstraint(
            "status <> 'open' OR terminal_event_published_at IS NULL",
            name=op.f("ck_tickets_event_published"),
        ),
    )
    op.create_index(
        "uq_tickets_active_update",
        "tickets",
        ["update_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_tickets_open_expires",
        "tickets",
        ["expires_at", "id"],
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_tickets_unpublished",
        "tickets",
        ["id"],
        postgresql_where=sa.text("status <> 'open' AND terminal_event_published_at IS NULL"),
    )

    op.create_table(
        "answers",
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("expert_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id", name=op.f("pk_answers")),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_answers_ticket_id_tickets"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            r"btrim(body, E' \t\r\n') <> '' AND char_length(body) <= 5000",
            name=op.f("ck_answers_body"),
        ),
    )


def downgrade() -> None:
    op.drop_table("answers")
    op.drop_table("tickets")
