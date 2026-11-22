"""add item slug and plant slug

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"

SLUG_REGEX = "^[a-z0-9-]{1,64}$"
OLD_KIND = (
    "(item_type = 'pack' AND plant_species IS NOT NULL AND seed_count IS NOT NULL)"
    " OR (item_type = 'sensor' AND plant_species IS NULL AND seed_count IS NULL)"
)
NEW_KIND = (
    "(item_type = 'pack' AND plant_species IS NOT NULL AND seed_count IS NOT NULL"
    " AND plant_slug IS NOT NULL)"
    " OR (item_type = 'sensor' AND plant_species IS NULL AND seed_count IS NULL"
    " AND plant_slug IS NULL)"
)


def slugify(column: str) -> str:
    """SQL: lowercase, drop apostrophes, turn each other non-alphanumeric run into one dash."""
    unquoted = f"regexp_replace(lower({column}), '[''’]', '', 'g')"
    dashed = f"regexp_replace({unquoted}, '[^a-z0-9]+', '-', 'g')"
    return f"trim(both '-' from left(trim(both '-' from {dashed}), 64))"


def upgrade() -> None:
    op.add_column("catalog_items", sa.Column("slug", sa.Text(), nullable=True))
    op.add_column("catalog_items", sa.Column("plant_slug", sa.Text(), nullable=True))
    op.execute(
        f"UPDATE catalog_items SET slug = {slugify('name')},"
        f" plant_slug = CASE WHEN item_type = 'pack' THEN {slugify('plant_species')} END"
    )
    op.alter_column("catalog_items", "slug", nullable=False)
    op.create_unique_constraint(op.f("uq_catalog_items_slug"), "catalog_items", ["slug"])
    op.create_check_constraint(
        op.f("ck_catalog_items_slug"), "catalog_items", f"slug ~ '{SLUG_REGEX}'"
    )
    op.create_check_constraint(
        op.f("ck_catalog_items_plant_slug"),
        "catalog_items",
        f"plant_slug IS NULL OR plant_slug ~ '{SLUG_REGEX}'",
    )
    op.drop_constraint(op.f("ck_catalog_items_kind"), "catalog_items", type_="check")
    op.create_check_constraint(op.f("ck_catalog_items_kind"), "catalog_items", NEW_KIND)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_catalog_items_kind"), "catalog_items", type_="check")
    op.create_check_constraint(op.f("ck_catalog_items_kind"), "catalog_items", OLD_KIND)
    op.drop_constraint(op.f("ck_catalog_items_plant_slug"), "catalog_items", type_="check")
    op.drop_constraint(op.f("ck_catalog_items_slug"), "catalog_items", type_="check")
    op.drop_constraint(op.f("uq_catalog_items_slug"), "catalog_items", type_="unique")
    op.drop_column("catalog_items", "plant_slug")
    op.drop_column("catalog_items", "slug")
