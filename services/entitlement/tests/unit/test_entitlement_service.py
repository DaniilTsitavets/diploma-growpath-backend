import uuid

import pytest

from app.service.entitlements import EntitlementService
from app.service.model import Tier
from tests.unit.fakes import (
    COMMITTED,
    NOW,
    RELEASED,
    FakeUnitOfWork,
    make_ai_kind,
    make_expert_kind,
    make_tiers,
)

USER = uuid.uuid4()


def make_service(uow: FakeUnitOfWork) -> EntitlementService:
    return EntitlementService(lambda: uow, make_tiers(), make_ai_kind(), make_expert_kind())


@pytest.mark.parametrize(
    ("paid", "tier", "ai_limit", "expert_limit"),
    [
        (0, Tier.STARTER, 3, 0),
        (4000, Tier.HOBBYIST, 10, 2),
        (9000, Tier.PREMIUM, 30, 5),
    ],
)
def test_limits_follow_the_tier(paid: int, tier: Tier, ai_limit: int, expert_limit: int):
    uow = FakeUnitOfWork()
    uow.placed_orders.pay(USER, paid)

    entitlement = make_service(uow).get(USER)

    assert entitlement.tier is tier
    assert (entitlement.ai.limit, entitlement.expert.limit) == (ai_limit, expert_limit)


def test_usage_counts_only_reservations_that_hold_quota():
    uow = FakeUnitOfWork()
    uow.ai_reservations.seed(USER, status=COMMITTED)
    uow.ai_reservations.seed(USER)
    uow.ai_reservations.seed(USER, status=RELEASED)
    uow.ai_reservations.seed(USER, unexpired=False)
    uow.expert_reservations.seed(USER, status=COMMITTED)

    entitlement = make_service(uow).get(USER)

    assert (entitlement.ai.used, entitlement.expert.used) == (2, 1)


def test_users_do_not_share_totals_or_usage():
    uow = FakeUnitOfWork()
    uow.placed_orders.pay(uuid.uuid4(), 9000)
    uow.ai_reservations.seed(uuid.uuid4(), status=COMMITTED)

    entitlement = make_service(uow).get(USER)

    assert (entitlement.tier, entitlement.ai.used) == (Tier.STARTER, 0)


def test_lowest_tier_has_no_end_date_and_is_not_queried():
    uow = FakeUnitOfWork()

    entitlement = make_service(uow).get(USER)

    assert entitlement.tier_expires_at is None
    assert uow.placed_orders.expiry_queries == []


@pytest.mark.parametrize(("paid", "threshold"), [(4500, 4000), (9500, 9000)])
def test_end_date_is_looked_up_against_the_threshold_of_the_current_tier(paid: int, threshold: int):
    uow = FakeUnitOfWork()
    uow.placed_orders.pay(USER, paid)
    uow.placed_orders.tier_expiry = NOW

    entitlement = make_service(uow).get(USER)

    assert entitlement.tier_expires_at == NOW
    assert uow.placed_orders.expiry_queries == [(USER, paid, threshold)]
