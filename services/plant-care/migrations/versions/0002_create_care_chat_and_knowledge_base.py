"""create the knowledge base and the care chat tables

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"

SEARCH_VECTOR = (
    "setweight(to_tsvector('english', heading), 'A') || "
    "setweight(to_tsvector('english', body), 'B')"
)


def created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "kb_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("species", sa.Text(), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_documents")),
        sa.UniqueConstraint("slug", name=op.f("uq_kb_documents_slug")),
        sa.CheckConstraint("slug ~ '^[a-z0-9-]{1,64}$'", name=op.f("ck_kb_documents_slug")),
        sa.CheckConstraint("source_url LIKE 'https://%'", name=op.f("ck_kb_documents_source_url")),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_kb_documents_sha")),
        sa.CheckConstraint(
            "btrim(title) <> '' AND char_length(title) <= 200 AND char_length(species) <= 100"
            " AND char_length(source_title) <= 300 AND char_length(source_url) <= 1000",
            name=op.f("ck_kb_documents_lengths"),
        ),
    )

    op.create_table(
        "kb_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("anchor", sa.Text(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR, persisted=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_sections")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["kb_documents.id"],
            name=op.f("fk_kb_sections_document_id_kb_documents"),
        ),
        sa.UniqueConstraint(
            "document_id", "position", name=op.f("uq_kb_sections_document_position")
        ),
        sa.UniqueConstraint("document_id", "anchor", name=op.f("uq_kb_sections_document_anchor")),
        sa.CheckConstraint("position >= 1", name=op.f("ck_kb_sections_position")),
        sa.CheckConstraint(
            "btrim(heading) <> '' AND char_length(heading) <= 200"
            " AND btrim(body) <> '' AND char_length(body) <= 4000",
            name=op.f("ck_kb_sections_lengths"),
        ),
    )
    op.create_index(
        op.f("ix_kb_sections_search_vector"),
        "kb_sections",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "chat_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("ai_reservation_id", sa.Uuid(), nullable=False),
        sa.Column("model_effort", sa.Text(), nullable=False),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_turns")),
        sa.ForeignKeyConstraint(
            ["journal_id"], ["journals.id"], name=op.f("fk_chat_turns_journal_id_journals")
        ),
        sa.UniqueConstraint("ai_reservation_id", name=op.f("uq_chat_turns_ai_reservation_id")),
        sa.CheckConstraint(
            "outcome IN ('answered', 'not_in_kb', 'out_of_scope')",
            name=op.f("ck_chat_turns_outcome"),
        ),
        sa.CheckConstraint(
            "model_effort IN ('medium', 'high')", name=op.f("ck_chat_turns_model_effort")
        ),
        sa.CheckConstraint(
            "btrim(question) <> '' AND char_length(question) <= 1000"
            " AND btrim(answer) <> '' AND char_length(answer) <= 4000",
            name=op.f("ck_chat_turns_lengths"),
        ),
    )
    op.create_index(
        op.f("ix_chat_turns_journal_created"),
        "chat_turns",
        ["journal_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "chat_turn_sources",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("document_title", sa.Text(), nullable=False),
        sa.Column("section_heading", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("turn_id", "rank", name=op.f("pk_chat_turn_sources")),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["chat_turns.id"], name=op.f("fk_chat_turn_sources_turn_id_chat_turns")
        ),
        sa.CheckConstraint("rank BETWEEN 1 AND 4", name=op.f("ck_chat_turn_sources_rank")),
        sa.CheckConstraint(
            "char_length(document_title) <= 200 AND char_length(section_heading) <= 200"
            " AND char_length(source_url) <= 1000",
            name=op.f("ck_chat_turn_sources_lengths"),
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_turn_sources")
    op.drop_table("chat_turns")
    op.drop_table("kb_sections")
    op.drop_table("kb_documents")
