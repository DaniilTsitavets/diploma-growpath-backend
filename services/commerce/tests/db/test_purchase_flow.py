import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import CatalogItem, Order
from app.payment import MockPaymentGateway
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.errors import IdempotencyKeyReused, PaymentFailed
from app.service.model import PlaceOrder
from app.service.purchase import PurchaseService
from tests.unit.fakes import RecordingEvents


class Declining:
    def charge(self, user_id, order_id, amount_cents) -> None:
        raise PaymentFailed


def make_service(engine: Engine, payments=None, events=None) -> PurchaseService:
    unit_of_work = partial(SqlAlchemyUnitOfWork, sessionmaker(engine))
    return PurchaseService(
        unit_of_work, payments or MockPaymentGateway(), events or RecordingEvents()
    )


def stored_orders(engine: Engine, user_id: uuid.UUID) -> list[Order]:
    with Session(engine) as session:
        return list(session.scalars(select(Order).where(Order.user_id == user_id)))


def test_first_purchase_is_discounted_and_a_repeat_of_the_request_is_not_charged_again(
    engine: Engine, discounted_item: CatalogItem
):
    service = make_service(engine)
    command = PlaceOrder(uuid.uuid4(), discounted_item.id, "key-1")

    first = service.place_order(command)
    repeated = service.place_order(command)

    assert (first.created, first.order.total_paid_cents) == (True, 2000)
    assert (repeated.created, repeated.order.id) == (False, first.order.id)
    assert len(stored_orders(engine, command.user_id)) == 1


def test_published_flag_is_stored_only_when_the_broker_accepted_the_event(
    engine: Engine, discounted_item: CatalogItem
):
    events = RecordingEvents(fails=True)
    service = make_service(engine, events=events)
    user_id = uuid.uuid4()

    service.place_order(PlaceOrder(user_id, discounted_item.id, "key-1"))
    (unpublished,) = stored_orders(engine, user_id)
    events.fails = False
    service.place_order(PlaceOrder(user_id, discounted_item.id, "key-1"))
    (published,) = stored_orders(engine, user_id)

    assert unpublished.placed_event_published_at is None
    assert published.placed_event_published_at is not None
    assert [e.order_id for e in events.published] == [published.id]
    assert events.published[0].occurred_at == published.placed_at


def test_second_purchase_of_the_same_user_is_not_discounted(
    engine: Engine, discounted_item: CatalogItem
):
    service = make_service(engine)
    user_id = uuid.uuid4()
    service.place_order(PlaceOrder(user_id, discounted_item.id, "k1"))

    second = service.place_order(PlaceOrder(user_id, discounted_item.id, "k2"))

    assert (second.order.discount_cents, second.order.total_paid_cents) == (0, 2500)


def test_key_reused_for_another_item_is_rejected(engine: Engine, discounted_item: CatalogItem):
    service = make_service(engine)
    user_id = uuid.uuid4()
    service.place_order(PlaceOrder(user_id, discounted_item.id, "k1"))

    with pytest.raises(IdempotencyKeyReused):
        service.place_order(PlaceOrder(user_id, uuid.uuid4(), "k1"))


def test_declined_payment_leaves_no_order(engine: Engine, discounted_item: CatalogItem):
    user_id = uuid.uuid4()

    with pytest.raises(PaymentFailed):
        make_service(engine, Declining()).place_order(PlaceOrder(user_id, discounted_item.id, "k1"))

    assert stored_orders(engine, user_id) == []


def test_parallel_purchases_of_one_user_give_the_discount_once(
    engine: Engine, discounted_item: CatalogItem
):
    service = make_service(engine)
    user_id = uuid.uuid4()
    commands = [PlaceOrder(user_id, discounted_item.id, f"k{i}") for i in range(4)]

    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        placed = list(pool.map(service.place_order, commands))

    assert Counter(p.order.discount_cents for p in placed) == {500: 1, 0: 3}
    assert sorted(o.total_paid_cents for o in stored_orders(engine, user_id)) == [
        2000,
        2500,
        2500,
        2500,
    ]
