"""create catalog and orders

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("plant_species", sa.Text(), nullable=True),
        sa.Column("seed_count", sa.Integer(), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("first_purchase_discount_pct", sa.SmallInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_items")),
        sa.UniqueConstraint("name", name=op.f("uq_catalog_items_name")),
        sa.CheckConstraint(
            "item_type IN ('pack', 'sensor')", name=op.f("ck_catalog_items_item_type")
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 120", name=op.f("ck_catalog_items_name_length")
        ),
        sa.CheckConstraint(
            "char_length(plant_species) BETWEEN 1 AND 80 AND btrim(plant_species) <> ''",
            name=op.f("ck_catalog_items_plant_species"),
        ),
        sa.CheckConstraint("price_cents >= 0", name=op.f("ck_catalog_items_price_non_negative")),
        sa.CheckConstraint(
            "first_purchase_discount_pct BETWEEN 1 AND 99",
            name=op.f("ck_catalog_items_discount_pct"),
        ),
        sa.CheckConstraint(
            "(item_type = 'pack' AND plant_species IS NOT NULL AND seed_count IS NOT NULL)"
            " OR (item_type = 'sensor' AND plant_species IS NULL AND seed_count IS NULL)",
            name=op.f("ck_catalog_items_kind"),
        ),
        sa.CheckConstraint("seed_count > 0", name=op.f("ck_catalog_items_seed_count_positive")),
    )
    op.create_table(
        "catalog_item_labels",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["catalog_items.id"],
            name=op.f("fk_catalog_item_labels_item_id_catalog_items"),
        ),
        sa.PrimaryKeyConstraint("item_id", "label", name=op.f("pk_catalog_item_labels")),
        sa.CheckConstraint(
            "char_length(label) BETWEEN 1 AND 40",
            name=op.f("ck_catalog_item_labels_label_length"),
        ),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("list_price_cents", sa.Integer(), nullable=False),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column("total_paid_cents", sa.Integer(), nullable=False),
        sa.Column(
            "placed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("placed_event_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"], ["catalog_items.id"], name=op.f("fk_orders_item_id_catalog_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name=op.f("uq_orders_user_id_idempotency_key")
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 128",
            name=op.f("ck_orders_idempotency_key_length"),
        ),
        sa.CheckConstraint("list_price_cents >= 0", name=op.f("ck_orders_list_price_non_negative")),
        sa.CheckConstraint(
            "total_paid_cents = list_price_cents - discount_cents"
            " AND discount_cents >= 0 AND discount_cents <= list_price_cents",
            name=op.f("ck_orders_amounts"),
        ),
    )
    op.create_index(
        op.f("uq_orders_one_discount_per_user"),
        "orders",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("discount_cents > 0"),
    )
    op.create_index(
        op.f("ix_orders_unpublished"),
        "orders",
        ["placed_at"],
        postgresql_where=sa.text("placed_event_published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_unpublished"), table_name="orders")
    op.drop_index(op.f("uq_orders_one_discount_per_user"), table_name="orders")
    op.drop_table("orders")
    op.drop_table("catalog_item_labels")
    op.drop_table("catalog_items")
