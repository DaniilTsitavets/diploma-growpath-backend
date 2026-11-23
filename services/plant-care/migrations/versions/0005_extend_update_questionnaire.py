"""replace the update questionnaire with the extended one

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"

ENUM_ANSWERS = {
    "watering_method": ("through_drainage", "partial", "unknown"),
    "pot_diameter_class": ("lt_10", "10_15", "15_20", "20_30", "gt_30", "unknown"),
    "drainage_holes": ("yes", "no", "unknown"),
    "standing_water_over_1h": ("yes", "no", "unknown"),
    "dry_depth": ("not_dry", "about_2_3cm", "about_5cm", "whole_root_ball_dry", "unknown"),
    "pot_weight_relative": ("about_same", "noticeably_lighter", "very_light", "unknown"),
}
OLD_ANSWERS = {
    "drainage": ("good", "poor", "none"),
    "finger_test": ("dry", "slightly_moist", "wet"),
    "pot_weight": ("lighter", "as_expected", "heavier"),
}
NUMERIC_COLUMNS = {
    "pot_weight_after_watering_g": (10, 2),
    "pot_weight_now_g": (10, 2),
    "sensor_reading": (10, 2),
    "air_temp_c": (5, 2),
    "air_rh_pct": (5, 2),
    "air_temp_min_c": (5, 2),
    "air_temp_max_c": (5, 2),
    "air_rh_min_pct": (5, 2),
    "air_rh_max_pct": (5, 2),
}
RANGES = {
    "air_temp_c": (-50, 60),
    "air_temp_min_c": (-50, 60),
    "air_temp_max_c": (-50, 60),
    "air_rh_pct": (0, 100),
    "air_rh_min_pct": (0, 100),
    "air_rh_max_pct": (0, 100),
}
OTHER_COLUMNS = ("repotted_or_moved", "sensor_unit", "air_measured_at")


def in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def create_check(name: str, condition: str) -> None:
    op.create_check_constraint(op.f(f"ck_updates_{name}"), "updates", condition)


def upgrade() -> None:
    for column in ("watering_interval_days", *OLD_ANSWERS, "sensor_reading"):
        op.drop_column("updates", column)
    for column in ENUM_ANSWERS:
        op.add_column(
            "updates", sa.Column(column, sa.Text(), server_default="unknown", nullable=False)
        )
    for column, (precision, scale) in NUMERIC_COLUMNS.items():
        op.add_column("updates", sa.Column(column, sa.Numeric(precision, scale), nullable=True))
    op.add_column("updates", sa.Column("repotted_or_moved", sa.Text(), nullable=True))
    op.add_column("updates", sa.Column("sensor_unit", sa.Text(), nullable=True))
    op.add_column(
        "updates", sa.Column("air_measured_at", sa.DateTime(timezone=True), nullable=True)
    )

    for column, values in ENUM_ANSWERS.items():
        create_check(column, in_list(column, values))
    for column in ("pot_weight_after_watering_g", "pot_weight_now_g"):
        create_check(column, f"{column} > 0 AND {column} <= 200000")
    create_check("repotted_or_moved", "repotted_or_moved IN ('yes', 'no')")
    create_check("sensor_reading", "sensor_reading >= 0")
    create_check("sensor_unit", "sensor_unit IN ('vwc_pct', 'raw')")
    create_check("sensor_reading_unit", "(sensor_reading IS NULL) = (sensor_unit IS NULL)")
    create_check(
        "sensor_vwc_pct", "sensor_unit IS DISTINCT FROM 'vwc_pct' OR sensor_reading <= 100"
    )
    for column, (low, high) in RANGES.items():
        create_check(column, f"{column} BETWEEN {low} AND {high}")
    create_check("air_temp_range", "air_temp_min_c <= air_temp_max_c")
    create_check("air_rh_range", "air_rh_min_pct <= air_rh_max_pct")


def downgrade() -> None:
    for column in (*ENUM_ANSWERS, *NUMERIC_COLUMNS, *OTHER_COLUMNS):
        op.drop_column("updates", column)
    op.add_column("updates", sa.Column("watering_interval_days", sa.Integer(), nullable=True))
    create_check("watering_interval_days", "watering_interval_days BETWEEN 1 AND 365")
    for column, values in OLD_ANSWERS.items():
        op.add_column(
            "updates", sa.Column(column, sa.Text(), server_default=values[0], nullable=False)
        )
        op.alter_column("updates", column, server_default=None)
        create_check(column, in_list(column, values))
    op.add_column("updates", sa.Column("sensor_reading", sa.Numeric(10, 2), nullable=True))
    create_check("sensor_reading", "sensor_reading >= 0")
