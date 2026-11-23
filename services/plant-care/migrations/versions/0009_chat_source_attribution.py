"""keep the source name and licence in each chat citation snapshot

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.add_column("chat_turn_sources", sa.Column("source_name", sa.Text(), nullable=True))
    op.add_column("chat_turn_sources", sa.Column("license", sa.Text(), nullable=True))
    # Citations stored before this step: the knowledge base holds PlantSolve data only.
    op.execute(
        """
        UPDATE chat_turn_sources AS s
        SET license = d.license, source_name = btrim(split_part(d.source_title, ':', 1))
        FROM kb_sections AS k JOIN kb_documents AS d ON d.id = k.document_id
        WHERE k.id = s.section_id
        """
    )
    op.execute("UPDATE chat_turn_sources SET source_name = 'PlantSolve' WHERE source_name IS NULL")
    op.alter_column("chat_turn_sources", "source_name", nullable=False)
    op.create_check_constraint(
        op.f("ck_chat_turn_sources_attribution"),
        "chat_turn_sources",
        "btrim(source_name) <> '' AND char_length(source_name) <= 300"
        " AND (license IS NULL OR (btrim(license) <> '' AND char_length(license) <= 100))",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_chat_turn_sources_attribution"), "chat_turn_sources", type_="check")
    op.drop_column("chat_turn_sources", "license")
    op.drop_column("chat_turn_sources", "source_name")
