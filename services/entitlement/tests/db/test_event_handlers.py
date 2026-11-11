import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, insert, select

from app.messaging.codec import TICKET_EVENTS_IGNORED, order_routes, ticket_routes
from app.models import ExpertQuotaReservation, PlacedOrder
from app.service.consumer import QueueConsumer
from app.service.event_handlers import EventHandlers
from app.service.model import Tier
from app.service.quota import QuotaService, expert_quota_kind
from app.service.tiers import TierPolicy
from tests.unit.fakes import FakeQueue, order_placed_body, ticket_body

USER = uuid.uuid4()


@pytest.fixture
def consumers(unit_of_work: Callable) -> tuple[QueueConsumer, QueueConsumer, FakeQueue, FakeQueue]:
    limits = {Tier.STARTER: 1, Tier.HOBBYIST: 2, Tier.PREMIUM: 3}
    expert = QuotaService(
        expert_quota_kind(limits, timedelta(hours=24)), unit_of_work, TierPolicy(4000, 9000)
    )
    handlers = EventHandlers(unit_of_work, expert)
    orders, tickets = FakeQueue(), FakeQueue()
    return (
        QueueConsumer(orders, order_routes(handlers), frozenset(), 10, 0),
        QueueConsumer(tickets, ticket_routes(handlers), TICKET_EVENTS_IGNORED, 10, 0),
        orders,
        tickets,
    )


def reserve_expert(engine: Engine, user_id: uuid.UUID, **overrides) -> uuid.UUID:
    escalation_id = uuid.uuid4()
    values = {
        "escalation_id": escalation_id,
        "user_id": user_id,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        **overrides,
    }
    with engine.begin() as connection:
        connection.execute(insert(ExpertQuotaReservation).values(**values))
    return escalation_id


def expert_status(engine: Engine, escalation_id: uuid.UUID) -> str:
    with engine.connect() as connection:
        return connection.execute(
            select(ExpertQuotaReservation.status).where(
                ExpertQuotaReservation.escalation_id == escalation_id
            )
        ).scalar_one()


def order_rows(engine: Engine, order_id: str) -> list:
    with engine.connect() as connection:
        return connection.execute(
            select(PlacedOrder.user_id, PlacedOrder.total_paid_cents, PlacedOrder.placed_at).where(
                PlacedOrder.order_id == uuid.UUID(order_id)
            )
        ).all()


def test_order_placed_is_projected_once_even_when_delivered_twice(consumers, engine: Engine):
    order_consumer, _, orders, _ = consumers
    body = order_placed_body(
        user_id=str(USER), total_paid_cents=1800, placed_at="2026-11-01T10:30:00+00:00"
    )
    orders.send("order.placed", body)
    orders.send("order.placed", body)

    order_consumer.run_once()

    ((user_id, cents, placed_at),) = order_rows(engine, body["order_id"])
    assert (user_id, cents) == (USER, 1800)
    assert placed_at == datetime(2026, 11, 1, 10, 30, tzinfo=UTC)
    assert len(orders.deleted) == 2


def test_a_sensor_order_counts_like_any_other(consumers, engine: Engine):
    order_consumer, _, orders, _ = consumers
    body = order_placed_body(
        user_id=str(USER), item_type="sensor", plant_species=None, seed_count=None
    )
    orders.send("order.placed", body)

    order_consumer.run_once()

    assert len(order_rows(engine, body["order_id"])) == 1


def test_a_negative_amount_violates_the_check_and_the_message_stays_in_the_queue(
    consumers, engine: Engine
):
    order_consumer, _, orders, _ = consumers
    body = order_placed_body(total_paid_cents=-1)
    orders.send("order.placed", body)

    order_consumer.run_once()

    assert orders.deleted == []
    assert order_rows(engine, body["order_id"]) == []


def test_ticket_created_commits_the_reservation_even_after_expiry(consumers, engine: Engine):
    _, ticket_consumer, _, tickets = consumers
    past = datetime.now(UTC) - timedelta(hours=1)
    escalation_id = reserve_expert(
        engine, USER, created_at=past - timedelta(hours=23), expires_at=past
    )
    tickets.send("ticket.created", ticket_body(escalation_id, USER))
    tickets.send("ticket.created", ticket_body(escalation_id, USER))

    ticket_consumer.run_once()

    assert expert_status(engine, escalation_id) == "committed"
    assert len(tickets.deleted) == 2


def test_ticket_cancelled_releases_a_committed_reservation_and_a_late_created_changes_nothing(
    consumers, engine: Engine
):
    _, ticket_consumer, _, tickets = consumers
    escalation_id = reserve_expert(engine, USER, status="committed")
    tickets.send("ticket.cancelled", ticket_body(escalation_id, USER))
    tickets.send("ticket.created", ticket_body(escalation_id, USER))

    ticket_consumer.run_once()

    assert expert_status(engine, escalation_id) == "released"
    assert len(tickets.deleted) == 2


def test_ticket_cancelled_before_ticket_created_ends_released(consumers, engine: Engine):
    _, ticket_consumer, _, tickets = consumers
    escalation_id = reserve_expert(engine, USER)
    tickets.send("ticket.cancelled", ticket_body(escalation_id, USER))
    tickets.send("ticket.created", ticket_body(escalation_id, USER))

    ticket_consumer.run_once()

    assert expert_status(engine, escalation_id) == "released"


def test_ticket_events_of_another_user_leave_the_reservation_alone(consumers, engine: Engine):
    _, ticket_consumer, _, tickets = consumers
    escalation_id = reserve_expert(engine, uuid.uuid4())
    tickets.send("ticket.created", ticket_body(escalation_id, USER))
    tickets.send("ticket.cancelled", ticket_body(escalation_id, USER))

    ticket_consumer.run_once()

    assert expert_status(engine, escalation_id) == "reserved"
    assert len(tickets.deleted) == 2


def test_ticket_answered_changes_nothing(consumers, engine: Engine):
    _, ticket_consumer, _, tickets = consumers
    escalation_id = reserve_expert(engine, USER)
    tickets.send("ticket.answered", ticket_body(escalation_id, USER, answer_text="Water less."))

    ticket_consumer.run_once()

    assert expert_status(engine, escalation_id) == "reserved"
    assert len(tickets.deleted) == 1


def test_ticket_created_for_a_missing_reservation_does_not_create_one(consumers, engine: Engine):
    _, ticket_consumer, _, tickets = consumers
    escalation_id = uuid.uuid4()
    tickets.send("ticket.created", ticket_body(escalation_id, USER))

    ticket_consumer.run_once()

    with engine.connect() as connection:
        rows = connection.execute(
            select(func.count()).where(ExpertQuotaReservation.escalation_id == escalation_id)
        ).scalar_one()
    assert (rows, len(tickets.deleted)) == (0, 1)
