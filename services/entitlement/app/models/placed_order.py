import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlacedOrder(Base):
    """Projection of `order.placed`; the tier is a sum over these rows and is never stored."""

    __tablename__ = "placed_orders"
    __table_args__ = (
        CheckConstraint("total_paid_cents >= 0", name="total_paid_non_negative"),
        Index(
            "ix_placed_orders_user_placed",
            "user_id",
            "placed_at",
            postgresql_include=["total_paid_cents"],
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID]
    total_paid_cents: Mapped[int]
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
