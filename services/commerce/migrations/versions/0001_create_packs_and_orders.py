"""create packs and orders

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "packs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("species", sa.String(length=80), nullable=True),
        sa.Column("seed_count", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("price_cents >= 0", name="price_non_negative"),
        sa.CheckConstraint("seed_count >= 0", name="seed_count_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("pack_id", sa.Uuid(), nullable=False),
        sa.Column("list_price_cents", sa.Integer(), nullable=False),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column("total_paid_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discount_cents >= 0 AND discount_cents <= list_price_cents",
            name="discount_within_price",
        ),
        sa.CheckConstraint("list_price_cents >= 0", name="list_price_non_negative"),
        sa.CheckConstraint(
            "total_paid_cents = list_price_cents - discount_cents", name="total_matches_price"
        ),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["packs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_orders_user_id"), "orders", ["user_id"], unique=False)
    op.create_table(
        "pack_labels",
        sa.Column("pack_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["pack_id"], ["packs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pack_id", "label"),
    )


def downgrade() -> None:
    op.drop_table("pack_labels")
    op.drop_index(op.f("ix_orders_user_id"), table_name="orders")
    op.drop_table("orders")
    op.drop_table("packs")
