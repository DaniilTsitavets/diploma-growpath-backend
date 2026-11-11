import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Order(Base):
    """A paid order for one item; failed payments are never stored. Amounts are US cents."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 128", name="idempotency_key_length"
        ),
        CheckConstraint("list_price_cents >= 0", name="list_price_non_negative"),
        CheckConstraint(
            "total_paid_cents = list_price_cents - discount_cents"
            " AND discount_cents >= 0 AND discount_cents <= list_price_cents",
            name="amounts",
        ),
        Index(
            "uq_orders_one_discount_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("discount_cents > 0"),
        ),
        Index(
            "ix_orders_unpublished",
            "placed_at",
            postgresql_where=text("placed_event_published_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_items.id"))
    idempotency_key: Mapped[str]
    list_price_cents: Mapped[int]
    discount_cents: Mapped[int]
    total_paid_cents: Mapped[int]
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    placed_event_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
