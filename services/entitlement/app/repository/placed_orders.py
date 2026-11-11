import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PlacedOrder
from app.service.model import NewPlacedOrder

TIER_WINDOW = timedelta(days=30)


class SqlAlchemyPlacedOrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, order: NewPlacedOrder) -> bool:
        result = self._session.execute(
            insert(PlacedOrder)
            .values(
                order_id=order.order_id,
                user_id=order.user_id,
                total_paid_cents=order.total_paid_cents,
                placed_at=order.placed_at,
            )
            .on_conflict_do_nothing(index_elements=[PlacedOrder.order_id])
            .returning(PlacedOrder.order_id)
        )
        return result.scalar_one_or_none() is not None

    def window_total(self, user_id: uuid.UUID) -> int:
        return self._session.scalar(
            select(func.coalesce(func.sum(PlacedOrder.total_paid_cents), 0)).where(
                PlacedOrder.user_id == user_id,
                PlacedOrder.placed_at > func.now() - TIER_WINDOW,
            )
        )

    def tier_expires_at(self, user_id: uuid.UUID, total: int, threshold: int) -> datetime | None:
        window = (
            select(
                PlacedOrder.placed_at,
                func.sum(PlacedOrder.total_paid_cents)
                .over(order_by=(PlacedOrder.placed_at, PlacedOrder.order_id))
                .label("left_cents"),
            )
            .where(PlacedOrder.user_id == user_id, PlacedOrder.placed_at > func.now() - TIER_WINDOW)
            .subquery()
        )
        return self._session.scalar(
            select(window.c.placed_at + TIER_WINDOW)
            .where(total - window.c.left_cents < threshold)
            .order_by(window.c.placed_at)
            .limit(1)
        )
