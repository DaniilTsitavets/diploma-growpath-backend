"""add the watering facts of a plant to its profile

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"

COLUMNS = (
    sa.Column("dryness_threshold_pct", sa.SmallInteger(), nullable=True),
    sa.Column("drought_tolerance", sa.Text(), nullable=True),
    sa.Column("dormancy_shift", sa.Boolean(), nullable=True),
    sa.Column("soil_drainage", sa.Text(), nullable=True),
    sa.Column("temp_min_c", sa.Numeric(3, 1), nullable=True),
    sa.Column("temp_max_c", sa.Numeric(3, 1), nullable=True),
    sa.Column("rh_min_pct", sa.SmallInteger(), nullable=True),
    sa.Column("rh_max_pct", sa.SmallInteger(), nullable=True),
    sa.Column("light_need", sa.Text(), nullable=True),
)
CHECKS = {
    "dryness_threshold": "dryness_threshold_pct IN (25, 50, 100)",
    "drought_tolerance": "drought_tolerance IN ('low', 'medium', 'high')",
    "soil_drainage": "soil_drainage IN ('fast', 'good')",
    "light_need": "light_need IN ('low', 'medium', 'bright_indirect', 'direct_sun')",
    "temp_range": (
        "temp_min_c BETWEEN -50 AND 60 AND temp_max_c BETWEEN -50 AND 60"
        " AND (temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c <= temp_max_c)"
    ),
    "rh_range": (
        "rh_min_pct BETWEEN 0 AND 100 AND rh_max_pct BETWEEN 0 AND 100"
        " AND (rh_min_pct IS NULL OR rh_max_pct IS NULL OR rh_min_pct <= rh_max_pct)"
    ),
}


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("plant_profiles", column)
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_plant_profiles_{name}"), "plant_profiles", condition)


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(op.f(f"ck_plant_profiles_{name}"), "plant_profiles", type_="check")
    for column in COLUMNS:
        op.drop_column("plant_profiles", column.name)
