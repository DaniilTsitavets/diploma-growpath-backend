"""add the FAQ and the derived catalog labels to the plant profile

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0008"
down_revision = "0007"

LABEL = "[a-z0-9]+(-[a-z0-9]+)*"
CHECKS = {
    "faq": (
        "faq IS NULL OR (jsonb_typeof(faq) = 'array' AND jsonb_array_length(faq) BETWEEN 1 AND 10)"
    ),
    "labels": (
        "cardinality(labels) <= 20"
        " AND array_position(labels, NULL) IS NULL AND array_position(labels, '') IS NULL"
        f" AND array_to_string(labels, ',') ~ '^({LABEL}(,{LABEL})*)?$'"
    ),
}


def upgrade() -> None:
    op.add_column("plant_profiles", sa.Column("faq", JSONB(none_as_null=True), nullable=True))
    op.add_column(
        "plant_profiles",
        sa.Column(
            "labels", ARRAY(sa.Text()), nullable=False, server_default=sa.text("ARRAY[]::text[]")
        ),
    )
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_plant_profiles_{name}"), "plant_profiles", condition)


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(op.f(f"ck_plant_profiles_{name}"), "plant_profiles", type_="check")
    op.drop_column("plant_profiles", "labels")
    op.drop_column("plant_profiles", "faq")
