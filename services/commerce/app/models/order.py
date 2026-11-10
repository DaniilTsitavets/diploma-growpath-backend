import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Order(Base):
    """A paid order; failed payments are never stored. Amounts are in US cents."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("list_price_cents >= 0", name="list_price_non_negative"),
        CheckConstraint(
            "discount_cents >= 0 AND discount_cents <= list_price_cents",
            name="discount_within_price",
        ),
        CheckConstraint(
            "total_paid_cents = list_price_cents - discount_cents", name="total_matches_price"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    pack_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("packs.id"))
    list_price_cents: Mapped[int]
    discount_cents: Mapped[int]
    total_paid_cents: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
