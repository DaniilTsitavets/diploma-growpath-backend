"""store the watering assessment computed for each diagnosis attempt

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.add_column("diagnoses", sa.Column("watering_assessment", JSONB(none_as_null=True)))


def downgrade() -> None:
    op.drop_column("diagnoses", "watering_assessment")
