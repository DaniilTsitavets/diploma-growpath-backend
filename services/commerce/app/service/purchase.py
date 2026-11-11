import logging
import uuid
from collections.abc import Callable

from app.service.errors import DiscountAlreadyUsed, IdempotencyKeyReused, ItemNotFound
from app.service.model import Item, NewOrder, OrderData, PlacedOrder, PlaceOrder
from app.service.ports import PaymentGateway, UnitOfWork
from app.service.pricing import first_purchase_discount

logger = logging.getLogger(__name__)


class PurchaseService:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork], payments: PaymentGateway) -> None:
        self._unit_of_work = unit_of_work
        self._payments = payments

    def place_order(self, command: PlaceOrder) -> PlacedOrder:
        with self._unit_of_work() as uow:
            existing = uow.orders.find(command.user_id, command.idempotency_key)
            if existing is not None:
                return self._replayed(existing, command)

            item = uow.catalog.get_active(command.item_id)
            if item is None:
                raise ItemNotFound(command.item_id)

            order = self._store(uow, command, item)
            if order is None:
                return self._replayed(self._existing(uow, command), command)

            self._payments.charge(command.user_id, order.id, order.total_paid_cents)
            uow.commit()

        logger.info("order placed", extra={"order_id": str(order.id)})
        return PlacedOrder(order, created=True)

    def _store(self, uow: UnitOfWork, command: PlaceOrder, item: Item) -> OrderData | None:
        discount = self._first_purchase_discount(uow, command, item)
        try:
            return uow.orders.place(self._draft(command, item, discount))
        except DiscountAlreadyUsed:
            return uow.orders.place(self._draft(command, item, discount_cents=0))

    @staticmethod
    def _first_purchase_discount(uow: UnitOfWork, command: PlaceOrder, item: Item) -> int:
        if item.first_purchase_discount_pct is None:
            return 0
        if uow.orders.has_discounted_order(command.user_id):
            return 0
        return first_purchase_discount(item.price_cents, item.first_purchase_discount_pct)

    @staticmethod
    def _draft(command: PlaceOrder, item: Item, discount_cents: int) -> NewOrder:
        return NewOrder(
            id=uuid.uuid4(),
            user_id=command.user_id,
            item_id=item.id,
            idempotency_key=command.idempotency_key,
            list_price_cents=item.price_cents,
            discount_cents=discount_cents,
            total_paid_cents=item.price_cents - discount_cents,
        )

    @staticmethod
    def _existing(uow: UnitOfWork, command: PlaceOrder) -> OrderData:
        order = uow.orders.find(command.user_id, command.idempotency_key)
        if order is None:
            raise RuntimeError("an order conflicted on its idempotency key but cannot be read")
        return order

    @staticmethod
    def _replayed(order: OrderData, command: PlaceOrder) -> PlacedOrder:
        if order.item_id != command.item_id:
            raise IdempotencyKeyReused(command.idempotency_key)
        return PlacedOrder(order, created=False)
