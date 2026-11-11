import uuid
from datetime import timedelta

import pytest

from app.service.errors import (
    LockTimeout,
    QuotaExhausted,
    ReservationIdInUse,
    ReservationNotFound,
    ReservationUnusable,
)
from app.service.model import Tier
from tests.unit.fakes import (
    COMMITTED,
    RELEASED,
    RESERVED,
    FakeReservations,
    FakeUnitOfWork,
    make_ai_kind,
    make_expert_kind,
    make_quota,
)

USER = uuid.uuid4()
OTHER_USER = uuid.uuid4()

both_kinds = pytest.mark.parametrize("kind_name", ["ai", "expert"])


def setup(kind_name: str, **limits: int):
    kind = (make_ai_kind if kind_name == "ai" else make_expert_kind)(**limits)
    uow = FakeUnitOfWork()
    reservations: FakeReservations = (
        uow.ai_reservations if kind_name == "ai" else uow.expert_reservations
    )
    return make_quota(kind, uow), uow, reservations


@both_kinds
def test_new_reservation_is_stored_committed_and_locked_for_the_user(kind_name: str):
    quota, uow, reservations = setup(kind_name, starter=1)
    reservation_id = uuid.uuid4()

    reserved = quota.reserve(reservation_id, USER)

    assert reserved.created
    assert (reserved.reservation.id, reserved.reservation.status) == (reservation_id, RESERVED)
    assert reservations.locked_users == [USER]
    assert uow.commits == 1


@pytest.mark.parametrize(
    ("kind_name", "ttl"), [("ai", timedelta(minutes=5)), ("expert", timedelta(hours=24))]
)
def test_reservation_lives_for_the_ttl_of_its_quota(kind_name: str, ttl: timedelta):
    quota, _, reservations = setup(kind_name, starter=1)

    quota.reserve(uuid.uuid4(), USER)

    assert reservations.inserted_ttls == [ttl]


@both_kinds
@pytest.mark.parametrize("used", [1, 2])
def test_reservation_is_refused_at_the_ceiling(kind_name: str, used: int):
    quota, uow, reservations = setup(kind_name, starter=used)
    for _ in range(used):
        reservations.seed(USER, status=COMMITTED)

    with pytest.raises(QuotaExhausted):
        quota.reserve(uuid.uuid4(), USER)

    assert uow.commits == 0
    assert len(reservations.rows) == used


@both_kinds
def test_last_free_slot_can_be_reserved(kind_name: str):
    quota, _, reservations = setup(kind_name, starter=2)
    reservations.seed(USER, status=COMMITTED)

    assert quota.reserve(uuid.uuid4(), USER).created


@both_kinds
def test_released_and_expired_reservations_free_their_slot(kind_name: str):
    quota, _, reservations = setup(kind_name, starter=1)
    reservations.seed(USER, status=RELEASED)
    reservations.seed(USER, unexpired=False)

    assert quota.reserve(uuid.uuid4(), USER).created


@both_kinds
def test_other_users_usage_does_not_count(kind_name: str):
    quota, _, reservations = setup(kind_name, starter=1)
    reservations.seed(OTHER_USER, status=COMMITTED)

    assert quota.reserve(uuid.uuid4(), USER).created


@pytest.mark.parametrize(
    ("paid", "tier"),
    [(0, Tier.STARTER), (4000, Tier.HOBBYIST), (9000, Tier.PREMIUM)],
)
def test_ceiling_depends_on_the_tier_of_the_user(paid: int, tier: Tier):
    ceiling = {Tier.STARTER: 1, Tier.HOBBYIST: 2, Tier.PREMIUM: 3}[tier]
    quota, uow, reservations = setup("ai", starter=1, hobbyist=2, premium=3)
    uow.placed_orders.pay(USER, paid)
    for _ in range(ceiling - 1):
        reservations.seed(USER, status=COMMITTED)

    assert quota.reserve(uuid.uuid4(), USER).created
    with pytest.raises(QuotaExhausted):
        quota.reserve(uuid.uuid4(), USER)


@both_kinds
def test_zero_ceiling_refuses_every_reservation(kind_name: str):
    quota, _, _ = setup(kind_name, starter=0)

    with pytest.raises(QuotaExhausted):
        quota.reserve(uuid.uuid4(), USER)


@both_kinds
@pytest.mark.parametrize(
    ("status", "unexpired"), [(RESERVED, True), (COMMITTED, True), (COMMITTED, False)]
)
def test_replay_returns_the_reservation_without_spending_quota(
    kind_name: str, status, unexpired: bool
):
    quota, uow, reservations = setup(kind_name, starter=1)
    existing = reservations.seed(USER, status=status, unexpired=unexpired)

    reserved = quota.reserve(existing.id, USER)

    assert not reserved.created
    assert reserved.reservation == existing
    assert uow.commits == 0


@both_kinds
@pytest.mark.parametrize(
    ("status", "unexpired", "message"),
    [(RELEASED, True, "released"), (RESERVED, False, "expired")],
)
def test_replay_of_a_dead_reservation_is_a_conflict(
    kind_name: str, status, unexpired: bool, message: str
):
    quota, _, reservations = setup(kind_name, starter=5)
    existing = reservations.seed(USER, status=status, unexpired=unexpired)

    with pytest.raises(ReservationUnusable, match=message):
        quota.reserve(existing.id, USER)


@both_kinds
def test_replay_by_another_user_does_not_reveal_the_owner(kind_name: str):
    quota, _, reservations = setup(kind_name, starter=5)
    existing = reservations.seed(OTHER_USER)

    with pytest.raises(ReservationIdInUse) as error:
        quota.reserve(existing.id, USER)

    assert str(OTHER_USER) not in str(error.value)


@both_kinds
def test_losing_the_insert_race_for_an_id_returns_the_winner_of_the_same_user(kind_name: str):
    quota, uow, reservations = setup(kind_name, starter=5)
    reservation_id = uuid.uuid4()
    reservations.insert_loses_to = reservations.seed(USER, reservation_id=reservation_id)

    reserved = quota.reserve(reservation_id, USER)

    assert not reserved.created
    assert uow.commits == 0


@both_kinds
def test_losing_the_insert_race_for_an_id_to_another_user_is_a_conflict(kind_name: str):
    quota, _, reservations = setup(kind_name, starter=5)
    reservation_id = uuid.uuid4()
    reservations.insert_loses_to = reservations.seed(OTHER_USER, reservation_id=reservation_id)

    with pytest.raises(ReservationIdInUse):
        quota.reserve(reservation_id, USER)


@both_kinds
def test_lock_timeout_propagates_and_nothing_is_stored(kind_name: str):
    quota, uow, reservations = setup(kind_name, starter=5)
    reservations.lock_times_out = True

    with pytest.raises(LockTimeout):
        quota.reserve(uuid.uuid4(), USER)

    assert (uow.commits, reservations.rows) == (0, {})


@both_kinds
def test_commit_moves_a_reserved_reservation_and_commits_the_transaction(kind_name: str):
    quota, uow, reservations = setup(kind_name)
    existing = reservations.seed(USER)

    committed = quota.commit(existing.id, USER)

    assert committed.status is COMMITTED
    assert uow.commits == 1


@both_kinds
def test_repeated_commit_succeeds_without_another_transaction(kind_name: str):
    quota, uow, reservations = setup(kind_name)
    existing = reservations.seed(USER, status=COMMITTED)

    assert quota.commit(existing.id, USER).status is COMMITTED
    assert uow.commits == 0


@both_kinds
def test_commit_of_a_released_reservation_is_a_conflict(kind_name: str):
    quota, _, reservations = setup(kind_name)
    existing = reservations.seed(USER, status=RELEASED)

    with pytest.raises(ReservationUnusable, match="released"):
        quota.commit(existing.id, USER)


@pytest.mark.parametrize(("kind_name", "commits_after_expiry"), [("ai", False), ("expert", True)])
def test_only_the_expert_reservation_can_be_committed_after_it_expired(
    kind_name: str, commits_after_expiry: bool
):
    quota, _, reservations = setup(kind_name)
    existing = reservations.seed(USER, unexpired=False)

    if commits_after_expiry:
        assert quota.commit(existing.id, USER).status is COMMITTED
    else:
        with pytest.raises(ReservationUnusable, match="expired"):
            quota.commit(existing.id, USER)
    assert reservations.last_transition_unexpired_only is not commits_after_expiry


@both_kinds
@pytest.mark.parametrize("action", ["commit", "release"])
@pytest.mark.parametrize("owner", [OTHER_USER, None], ids=["foreign", "missing"])
def test_foreign_or_missing_reservation_is_not_found(kind_name: str, action: str, owner):
    quota, _, reservations = setup(kind_name)
    reservation_id = reservations.seed(owner).id if owner else uuid.uuid4()

    with pytest.raises(ReservationNotFound) as error:
        getattr(quota, action)(reservation_id, USER)

    assert str(OTHER_USER) not in str(error.value)


@both_kinds
def test_release_frees_a_reserved_reservation(kind_name: str):
    quota, uow, reservations = setup(kind_name)
    existing = reservations.seed(USER)

    assert quota.release(existing.id, USER).status is RELEASED
    assert uow.commits == 1


@both_kinds
def test_repeated_release_succeeds(kind_name: str):
    quota, _, reservations = setup(kind_name)
    existing = reservations.seed(USER, status=RELEASED)

    assert quota.release(existing.id, USER).status is RELEASED


@both_kinds
def test_rest_release_of_a_committed_reservation_is_a_conflict(kind_name: str):
    quota, uow, reservations = setup(kind_name)
    existing = reservations.seed(USER, status=COMMITTED)

    with pytest.raises(ReservationUnusable, match="committed"):
        quota.release(existing.id, USER)

    assert uow.commits == 0


@pytest.mark.parametrize(
    ("kind_name", "status", "released"),
    [
        ("expert", RESERVED, True),
        ("expert", COMMITTED, True),
        ("expert", RELEASED, True),
        ("ai", RESERVED, True),
        ("ai", COMMITTED, False),
    ],
)
def test_event_release_follows_the_quota_kind(kind_name: str, status, released: bool):
    quota, _, reservations = setup(kind_name)
    existing = reservations.seed(USER, status=status)

    if released:
        assert quota.release_by_event(existing.id, USER).status is RELEASED
    else:
        with pytest.raises(ReservationUnusable, match="committed"):
            quota.release_by_event(existing.id, USER)


def test_event_release_of_a_foreign_reservation_is_not_found():
    quota, _, reservations = setup("expert")
    existing = reservations.seed(OTHER_USER, status=COMMITTED)

    with pytest.raises(ReservationNotFound):
        quota.release_by_event(existing.id, USER)
