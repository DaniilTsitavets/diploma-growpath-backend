"""create placed orders and quota reservations

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def create_reservation_table(table: str, key: str) -> None:
    op.create_table(
        table,
        sa.Column(key, sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="reserved", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint(key, name=op.f(f"pk_{table}")),
        sa.CheckConstraint(
            "status IN ('reserved', 'committed', 'released')", name=op.f(f"ck_{table}_status")
        ),
        sa.CheckConstraint("expires_at > created_at", name=op.f(f"ck_{table}_expiry")),
    )
    op.create_index(op.f(f"ix_{table}_user_created"), table, ["user_id", "created_at"])


def upgrade() -> None:
    op.create_table(
        "placed_orders",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("total_paid_cents", sa.Integer(), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_placed_orders")),
        sa.CheckConstraint(
            "total_paid_cents >= 0", name=op.f("ck_placed_orders_total_paid_non_negative")
        ),
    )
    op.create_index(
        op.f("ix_placed_orders_user_placed"),
        "placed_orders",
        ["user_id", "placed_at"],
        postgresql_include=["total_paid_cents"],
    )
    create_reservation_table("ai_quota_reservations", "reservation_id")
    create_reservation_table("expert_quota_reservations", "escalation_id")


def downgrade() -> None:
    op.drop_table("expert_quota_reservations")
    op.drop_table("ai_quota_reservations")
    op.drop_table("placed_orders")
