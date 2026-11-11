import uuid

import pytest

from app.service.errors import IdempotencyKeyReused, ItemNotFound, PaymentFailed
from app.service.model import PlaceOrder
from app.service.purchase import PurchaseService
from tests.unit.fakes import (
    FakeCatalog,
    FakeOrders,
    FakeUnitOfWork,
    RecordingEvents,
    RecordingPayments,
    make_item,
    make_order_data,
)


class Scenario:
    def __init__(self, *items, declines: bool = False, publish_fails: bool = False) -> None:
        self.orders = FakeOrders()
        self.payments = RecordingPayments(declines)
        self.events = RecordingEvents(publish_fails)
        self.uow = FakeUnitOfWork(self.orders, FakeCatalog(*items))
        self.service = PurchaseService(lambda: self.uow, self.payments, self.events)

    def place(self, item, user_id=None, key="key-1"):
        command = PlaceOrder(user_id or uuid.uuid4(), item.id, key)
        return self.service.place_order(command)


def test_first_purchase_of_a_discounted_item_gets_the_discount():
    item = make_item()
    scenario = Scenario(item)

    placed = scenario.place(item)

    assert placed.created is True
    assert (placed.order.list_price_cents, placed.order.discount_cents) == (2500, 500)
    assert placed.order.total_paid_cents == 2000
    assert scenario.payments.charges == [2000]
    assert scenario.uow.committed is True


def test_user_who_already_had_a_discount_pays_the_full_price():
    item = make_item()
    scenario = Scenario(item)
    user_id = uuid.uuid4()
    scenario.place(item, user_id, key="first")

    second = scenario.place(item, user_id, key="second")

    assert (second.order.discount_cents, second.order.total_paid_cents) == (0, 2500)
    assert scenario.payments.charges == [2000, 2500]


def test_item_without_a_discount_percent_is_sold_at_list_price():
    item = make_item(first_purchase_discount_pct=None)
    scenario = Scenario(item)

    placed = scenario.place(item)

    assert (placed.order.discount_cents, placed.order.total_paid_cents) == (0, 2500)


def test_discount_rounding_to_zero_cents_is_not_recorded():
    item = make_item(price_cents=4)
    scenario = Scenario(item)

    placed = scenario.place(item)

    assert (placed.order.discount_cents, placed.order.total_paid_cents) == (0, 4)


def test_losing_the_discount_race_charges_the_full_price():
    item = make_item()
    scenario = Scenario(item)
    scenario.orders.rejects_discount = True

    placed = scenario.place(item)

    assert (placed.order.discount_cents, placed.order.total_paid_cents) == (0, 2500)
    assert scenario.payments.charges == [2500]


def test_repeated_request_returns_the_existing_order_without_charging_again():
    item = make_item()
    scenario = Scenario(item)
    user_id = uuid.uuid4()
    first = scenario.place(item, user_id)

    repeated = scenario.place(item, user_id)

    assert repeated.created is False
    assert repeated.order.id == first.order.id
    assert scenario.payments.charges == [2000]


def test_key_used_for_another_item_is_rejected_without_charging():
    item, other = make_item(), make_item(name="Mint Kit")
    scenario = Scenario(item, other)
    user_id = uuid.uuid4()
    scenario.place(item, user_id)

    with pytest.raises(IdempotencyKeyReused):
        scenario.place(other, user_id)

    assert scenario.payments.charges == [2000]


@pytest.mark.parametrize("same_item", [True, False], ids=["same item", "another item"])
def test_concurrent_request_with_the_same_key_is_resolved_by_reading_the_winner(same_item: bool):
    item = make_item()
    scenario = Scenario(item)
    user_id = uuid.uuid4()
    winner_item_id = item.id if same_item else uuid.uuid4()
    scenario.orders.concurrent_winner = make_order_data(
        user_id=user_id, item_id=winner_item_id, idempotency_key="key-1"
    )

    if same_item:
        placed = scenario.place(item, user_id)
        assert placed.created is False
        assert placed.order.id == scenario.orders.concurrent_winner.id
    else:
        with pytest.raises(IdempotencyKeyReused):
            scenario.place(item, user_id)
        assert scenario.events.published == []

    assert scenario.payments.charges == []


@pytest.mark.parametrize("active", [False, None], ids=["inactive item", "unknown item"])
def test_unavailable_item_is_not_sold(active):
    item = make_item(is_active=bool(active))
    scenario = Scenario(*([item] if active is False else []))

    with pytest.raises(ItemNotFound):
        scenario.place(item)

    assert scenario.payments.charges == []
    assert scenario.uow.committed is False


def test_declined_payment_is_not_committed_and_publishes_nothing():
    item = make_item()
    scenario = Scenario(item, declines=True)

    with pytest.raises(PaymentFailed):
        scenario.place(item)

    assert scenario.uow.committed is False
    assert scenario.events.published == []


def test_placed_order_is_published_with_the_contract_fields_and_marked():
    item = make_item()
    scenario = Scenario(item)

    placed = scenario.place(item)

    (event,) = scenario.events.published
    order = placed.order
    assert (event.event_id, event.order_id, event.user_id, event.item_id) == (
        order.id,
        order.id,
        order.user_id,
        item.id,
    )
    assert (event.item_type, event.plant_species, event.seed_count) == ("pack", "Basil", 5)
    assert (event.total_paid_cents, event.discount_applied) == (2000, True)
    assert event.occurred_at == event.placed_at == order.placed_at
    assert scenario.orders.stored[0].placed_event_published_at is not None


def test_sensor_event_has_no_species_or_seed_count():
    sensor = make_item(
        item_type="sensor",
        plant_species=None,
        seed_count=None,
        first_purchase_discount_pct=None,
    )
    scenario = Scenario(sensor)

    scenario.place(sensor)

    (event,) = scenario.events.published
    assert (event.item_type, event.plant_species, event.seed_count) == ("sensor", None, None)
    assert event.discount_applied is False


def test_broker_failure_does_not_fail_the_order_and_leaves_it_unpublished():
    item = make_item()
    scenario = Scenario(item, publish_fails=True)

    placed = scenario.place(item)

    assert placed.created is True
    assert scenario.uow.committed is True
    assert scenario.orders.stored[0].placed_event_published_at is None


def test_failed_flag_update_does_not_fail_the_order():
    item = make_item()
    scenario = Scenario(item)
    scenario.orders.mark_fails = True

    placed = scenario.place(item)

    assert placed.created is True
    assert len(scenario.events.published) == 1


def test_repeated_request_republishes_an_event_even_if_the_item_was_deactivated():
    item = make_item()
    scenario = Scenario(item, publish_fails=True)
    user_id = uuid.uuid4()
    first = scenario.place(item, user_id)
    scenario.events.fails = False
    scenario.uow.catalog.deactivate(item.id)

    repeated = scenario.place(item, user_id)

    (event,) = scenario.events.published
    assert repeated.created is False
    assert (event.event_id, event.plant_species) == (first.order.id, "Basil")
    assert scenario.orders.stored[0].placed_event_published_at is not None


def test_repeated_request_does_not_publish_an_event_that_is_already_out():
    item = make_item()
    scenario = Scenario(item)
    user_id = uuid.uuid4()
    scenario.place(item, user_id)

    scenario.place(item, user_id)

    assert len(scenario.events.published) == 1
