import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, insert, select

from app.models import AiQuotaReservation, ExpertQuotaReservation
from app.service.entitlements import EntitlementService
from app.service.errors import (
    QuotaExhausted,
    ReservationIdInUse,
    ReservationNotFound,
    ReservationUnusable,
)
from app.service.model import NewPlacedOrder, ReservationStatus, Tier
from app.service.quota import QuotaService, ai_quota_kind, expert_quota_kind
from app.service.tiers import TierPolicy
from tests.db.factories import key_name

TIERS = TierPolicy(hobbyist_threshold_cents=4000, premium_threshold_cents=9000)
RESERVED = ReservationStatus.RESERVED
COMMITTED = ReservationStatus.COMMITTED
RELEASED = ReservationStatus.RELEASED


def limits(starter: int, hobbyist: int = 10, premium: int = 30) -> dict[Tier, int]:
    return {Tier.STARTER: starter, Tier.HOBBYIST: hobbyist, Tier.PREMIUM: premium}


@pytest.fixture
def ai_quota(unit_of_work: Callable) -> QuotaService:
    return QuotaService(ai_quota_kind(limits(2), timedelta(minutes=5)), unit_of_work, TIERS)


@pytest.fixture
def expert_quota(unit_of_work: Callable) -> QuotaService:
    return QuotaService(
        expert_quota_kind(limits(1, 2, 3), timedelta(hours=24)), unit_of_work, TIERS
    )


def insert_reservation(engine: Engine, model, user_id: uuid.UUID, **overrides) -> uuid.UUID:
    """Store a row the service cannot create, such as an already expired reservation."""
    reservation_id = uuid.uuid4()
    values = {
        key_name(model): reservation_id,
        "user_id": user_id,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        **overrides,
    }
    with engine.begin() as connection:
        connection.execute(insert(model).values(**values))
    return reservation_id


def stored_status(engine: Engine, model, reservation_id: uuid.UUID) -> str:
    key = getattr(model, key_name(model))
    with engine.connect() as connection:
        return connection.execute(select(model.status).where(key == reservation_id)).scalar_one()


def test_ai_reservation_lives_through_reserve_replay_commit(ai_quota: QuotaService, engine: Engine):
    user_id, reservation_id = uuid.uuid4(), uuid.uuid4()

    created = ai_quota.reserve(reservation_id, user_id)
    replayed = ai_quota.reserve(reservation_id, user_id)
    committed = ai_quota.commit(reservation_id, user_id)
    replayed_after_commit = ai_quota.reserve(reservation_id, user_id)

    assert (created.created, replayed.created) == (True, False)
    assert replayed.reservation.status is RESERVED
    assert committed.status is COMMITTED
    assert replayed_after_commit.reservation.status is COMMITTED
    assert stored_status(engine, AiQuotaReservation, reservation_id) == "committed"


def test_the_ceiling_cannot_be_exceeded_and_a_release_frees_a_slot(ai_quota: QuotaService):
    user_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    ai_quota.reserve(first, user_id)
    ai_quota.reserve(second, user_id)

    with pytest.raises(QuotaExhausted):
        ai_quota.reserve(uuid.uuid4(), user_id)
    ai_quota.release(first, user_id)

    assert ai_quota.reserve(uuid.uuid4(), user_id).created
    with pytest.raises(ReservationUnusable, match="released"):
        ai_quota.reserve(first, user_id)


def test_the_ceiling_follows_the_tier_computed_from_paid_orders(
    expert_quota: QuotaService, unit_of_work: Callable
):
    user_id = uuid.uuid4()
    with unit_of_work() as uow:
        uow.placed_orders.add(NewPlacedOrder(uuid.uuid4(), user_id, 4000, datetime.now(UTC)))
        uow.commit()

    expert_quota.reserve(uuid.uuid4(), user_id)
    expert_quota.reserve(uuid.uuid4(), user_id)
    with pytest.raises(QuotaExhausted):
        expert_quota.reserve(uuid.uuid4(), user_id)


def test_a_starter_without_expert_quota_is_refused(unit_of_work: Callable):
    quota = QuotaService(expert_quota_kind(limits(0), timedelta(hours=24)), unit_of_work, TIERS)

    with pytest.raises(QuotaExhausted):
        quota.reserve(uuid.uuid4(), uuid.uuid4())


def test_a_reservation_id_of_another_user_is_refused(ai_quota: QuotaService):
    reservation_id = uuid.uuid4()
    ai_quota.reserve(reservation_id, uuid.uuid4())

    with pytest.raises(ReservationIdInUse):
        ai_quota.reserve(reservation_id, uuid.uuid4())


@pytest.mark.parametrize("action", ["commit", "release"])
def test_another_users_reservation_is_not_found(ai_quota: QuotaService, action: str):
    reservation_id = uuid.uuid4()
    ai_quota.reserve(reservation_id, uuid.uuid4())

    with pytest.raises(ReservationNotFound):
        getattr(ai_quota, action)(reservation_id, uuid.uuid4())


def test_an_expired_ai_reservation_is_neither_replayed_nor_committed(
    ai_quota: QuotaService, engine: Engine
):
    user_id = uuid.uuid4()
    past = datetime.now(UTC) - timedelta(hours=1)
    expired = insert_reservation(
        engine, AiQuotaReservation, user_id, created_at=past - timedelta(minutes=5), expires_at=past
    )

    with pytest.raises(ReservationUnusable, match="expired"):
        ai_quota.reserve(expired, user_id)
    with pytest.raises(ReservationUnusable, match="expired"):
        ai_quota.commit(expired, user_id)
    assert stored_status(engine, AiQuotaReservation, expired) == "reserved"


def test_an_expired_reservation_does_not_hold_quota(ai_quota: QuotaService, engine: Engine):
    user_id = uuid.uuid4()
    past = datetime.now(UTC) - timedelta(hours=1)
    for _ in range(2):
        insert_reservation(
            engine,
            AiQuotaReservation,
            user_id,
            created_at=past - timedelta(minutes=5),
            expires_at=past,
        )

    assert ai_quota.reserve(uuid.uuid4(), user_id).created


def test_an_expert_reservation_is_committed_after_expiry_and_released_only_by_an_event(
    expert_quota: QuotaService, engine: Engine
):
    user_id = uuid.uuid4()
    past = datetime.now(UTC) - timedelta(hours=1)
    escalation = insert_reservation(
        engine,
        ExpertQuotaReservation,
        user_id,
        created_at=past - timedelta(hours=23),
        expires_at=past,
    )

    committed = expert_quota.commit(escalation, user_id)
    with pytest.raises(ReservationUnusable, match="committed"):
        expert_quota.release(escalation, user_id)
    released = expert_quota.release_by_event(escalation, user_id)

    assert (committed.status, released.status) == (COMMITTED, RELEASED)
    assert expert_quota.release(escalation, user_id).status is RELEASED
    with pytest.raises(ReservationUnusable, match="released"):
        expert_quota.commit(escalation, user_id)


def test_entitlement_reflects_orders_and_reservations(
    unit_of_work: Callable, ai_quota: QuotaService
):
    user_id = uuid.uuid4()
    with unit_of_work() as uow:
        uow.placed_orders.add(
            NewPlacedOrder(uuid.uuid4(), user_id, 5000, datetime.now(UTC) - timedelta(days=2))
        )
        uow.commit()
    reservation_id = uuid.uuid4()
    ai_quota.reserve(reservation_id, user_id)
    ai_quota.commit(reservation_id, user_id)
    service = EntitlementService(
        unit_of_work,
        TIERS,
        ai_quota_kind(limits(2), timedelta(minutes=5)),
        expert_quota_kind(limits(1, 2, 3), timedelta(hours=24)),
    )

    entitlement = service.get(user_id)

    assert entitlement.tier is Tier.HOBBYIST
    assert (entitlement.ai.used, entitlement.ai.limit) == (1, 10)
    assert (entitlement.expert.used, entitlement.expert.limit) == (0, 2)
    assert abs(entitlement.tier_expires_at - (datetime.now(UTC) + timedelta(days=28))) < timedelta(
        minutes=1
    )
