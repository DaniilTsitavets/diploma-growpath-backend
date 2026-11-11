import logging
import uuid
from collections.abc import Callable

from app.service.errors import (
    DiscountAlreadyUsed,
    IdempotencyKeyReused,
    ItemNotFound,
    PublishFailed,
)
from app.service.events import order_placed
from app.service.model import Item, NewOrder, OrderData, PlacedOrder, PlaceOrder
from app.service.ports import OrderEvents, PaymentGateway, UnitOfWork
from app.service.pricing import first_purchase_discount

logger = logging.getLogger(__name__)


class PurchaseService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        payments: PaymentGateway,
        events: OrderEvents,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._payments = payments
        self._events = events

    def place_order(self, command: PlaceOrder) -> PlacedOrder:
        with self._unit_of_work() as uow:
            placed, unpublished_item = self._place(uow, command)

        if placed.created:
            logger.info("order placed", extra={"order_id": str(placed.order.id)})
        if unpublished_item is not None:
            self._publish(placed.order, unpublished_item)
        return placed

    def _place(self, uow: UnitOfWork, command: PlaceOrder) -> tuple[PlacedOrder, Item | None]:
        """The placed order and, if its event still has to go out, the item the event needs."""
        existing = uow.orders.find(command.user_id, command.idempotency_key)
        if existing is not None:
            return self._replayed(uow, existing, command)

        item = uow.catalog.get_active(command.item_id)
        if item is None:
            raise ItemNotFound(command.item_id)

        order = self._store(uow, command, item)
        if order is None:
            return self._replayed(uow, self._existing(uow, command), command)

        self._payments.charge(command.user_id, order.id, order.total_paid_cents)
        uow.commit()
        return PlacedOrder(order, created=True), item

    def _store(self, uow: UnitOfWork, command: PlaceOrder, item: Item) -> OrderData | None:
        discount = self._first_purchase_discount(uow, command, item)
        try:
            return uow.orders.place(self._draft(command, item, discount))
        except DiscountAlreadyUsed:
            return uow.orders.place(self._draft(command, item, discount_cents=0))

    def _publish(self, order: OrderData, item: Item) -> None:
        """Runs after the commit: the order is paid, so a broker failure must not fail the request.

        The unset published flag keeps the order visible to the republisher.
        """
        try:
            self._events.publish_order_placed(order_placed(order, item))
        except PublishFailed:
            logger.exception(
                "order.placed was not published, it stays pending",
                extra={"order_id": str(order.id)},
            )
            return
        self._mark_published(order.id)

    def _mark_published(self, order_id: uuid.UUID) -> None:
        try:
            with self._unit_of_work() as uow:
                uow.orders.mark_published(order_id)
                uow.commit()
        except Exception:
            # The event is already out. A lost flag only causes a duplicate publication, which
            # consumers drop by event id, and it must not turn a paid order into an error.
            logger.exception(
                "could not mark order.placed as published", extra={"order_id": str(order_id)}
            )

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
    def _replayed(
        uow: UnitOfWork, order: OrderData, command: PlaceOrder
    ) -> tuple[PlacedOrder, Item | None]:
        if order.item_id != command.item_id:
            raise IdempotencyKeyReused(command.idempotency_key)
        item = None if order.placed_event_published_at else uow.catalog.get(order.item_id)
        return PlacedOrder(order, created=False), item
