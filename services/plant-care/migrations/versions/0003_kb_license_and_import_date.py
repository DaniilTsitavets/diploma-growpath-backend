"""store the licence and the import date of each knowledge document

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.add_column("kb_documents", sa.Column("license", sa.Text(), nullable=True))
    op.add_column("kb_documents", sa.Column("imported_on", sa.Date(), nullable=True))
    op.create_check_constraint(
        op.f("ck_kb_documents_license"),
        "kb_documents",
        "license IS NULL OR (btrim(license) <> '' AND char_length(license) <= 100)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_kb_documents_license"), "kb_documents", type_="check")
    op.drop_column("kb_documents", "imported_on")
    op.drop_column("kb_documents", "license")
