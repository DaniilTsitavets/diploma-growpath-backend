import uuid

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order
from app.service.errors import DiscountAlreadyUsed
from app.service.model import NewOrder, OrderData

DISCOUNT_INDEX = "uq_orders_one_discount_per_user"
ORDER_COLUMNS = tuple(Order.__table__.c)


def to_order_data(row) -> OrderData:
    return OrderData(**row._mapping)


class SqlAlchemyOrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, user_id: uuid.UUID, idempotency_key: str) -> OrderData | None:
        row = self._session.execute(
            select(*ORDER_COLUMNS).where(
                Order.user_id == user_id, Order.idempotency_key == idempotency_key
            )
        ).one_or_none()
        return None if row is None else to_order_data(row)

    def has_discounted_order(self, user_id: uuid.UUID) -> bool:
        return self._session.scalar(
            select(exists().where(Order.user_id == user_id, Order.discount_cents > 0))
        )

    def mark_published(self, order_id: uuid.UUID) -> bool:
        result = self._session.execute(
            update(Order)
            .where(Order.id == order_id, Order.placed_event_published_at.is_(None))
            .values(placed_event_published_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def place(self, order: NewOrder) -> OrderData | None:
        statement = (
            insert(Order)
            .values(
                id=order.id,
                user_id=order.user_id,
                item_id=order.item_id,
                idempotency_key=order.idempotency_key,
                list_price_cents=order.list_price_cents,
                discount_cents=order.discount_cents,
                total_paid_cents=order.total_paid_cents,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
            .returning(*ORDER_COLUMNS)
        )
        try:
            # The savepoint keeps the outer transaction usable after the index violation.
            with self._session.begin_nested():
                row = self._session.execute(statement).one_or_none()
        except IntegrityError as error:
            if error.orig.diag.constraint_name == DISCOUNT_INDEX:
                raise DiscountAlreadyUsed from error
            raise
        return None if row is None else to_order_data(row)
