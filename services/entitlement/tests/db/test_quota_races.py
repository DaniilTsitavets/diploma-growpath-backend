import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import Connection, Engine, insert, text

from app.models import AiQuotaReservation
from app.service.errors import LockTimeout, QuotaExhausted
from app.service.model import Tier
from app.service.quota import QuotaService, ai_quota_kind
from app.service.tiers import TierPolicy
from tests.db.factories import reservation_values

WAIT_SECONDS = 5
TIERS = TierPolicy(hobbyist_threshold_cents=4000, premium_threshold_cents=9000)
CEILING = 3
KIND = ai_quota_kind(
    {Tier.STARTER: CEILING, Tier.HOBBYIST: CEILING, Tier.PREMIUM: CEILING}, timedelta(minutes=5)
)
HOLD_LOCK = text(
    "SELECT pg_advisory_xact_lock(hashtextextended('ai:' || CAST(:user_id AS text), 0))"
)
ADVISORY_WAITERS = text(
    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
    " AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
)


def wait_until_a_reservation_waits_for_the_lock(watcher: Connection) -> None:
    """Poll until a backend waits for an advisory lock; no fixed delay is involved."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if watcher.execute(ADVISORY_WAITERS).scalar_one():
            return
    raise AssertionError("no reservation ever waited for the user lock")


def fill_quota(engine: Engine, user_id: uuid.UUID, used: int) -> None:
    with engine.begin() as connection:
        for _ in range(used):
            connection.execute(
                insert(AiQuotaReservation).values(
                    **reservation_values(AiQuotaReservation, user_id=user_id, status="committed")
                )
            )


def reserve(quota: QuotaService, user_id: uuid.UUID) -> str:
    try:
        quota.reserve(uuid.uuid4(), user_id)
    except QuotaExhausted:
        return "refused"
    return "granted"


def test_a_reservation_waits_for_the_one_in_progress_and_sees_its_row(
    engine: Engine, unit_of_work: Callable
):
    user_id = uuid.uuid4()
    fill_quota(engine, user_id, CEILING - 1)
    quota = QuotaService(KIND, unit_of_work, TIERS)
    with (
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher,
        ThreadPoolExecutor(max_workers=1) as pool,
        unit_of_work() as first,
    ):
        first.ai_reservations.lock_user(user_id)
        first.ai_reservations.insert(uuid.uuid4(), user_id, timedelta(minutes=5))
        second = pool.submit(reserve, quota, user_id)
        wait_until_a_reservation_waits_for_the_lock(watcher)
        first.commit()

    assert second.result(timeout=WAIT_SECONDS) == "refused"


@pytest.mark.parametrize("round_number", range(5))
def test_two_parallel_reservations_at_the_last_free_slot_grant_exactly_one(
    engine: Engine, unit_of_work: Callable, round_number: int
):
    user_id = uuid.uuid4()
    fill_quota(engine, user_id, CEILING - 1)
    quota = QuotaService(KIND, unit_of_work, TIERS)
    start_together = Barrier(2)

    def contender() -> str:
        start_together.wait(timeout=WAIT_SECONDS)
        return reserve(quota, user_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        contenders = [pool.submit(contender) for _ in range(2)]
        outcomes = [future.result(timeout=WAIT_SECONDS) for future in contenders]

    assert sorted(outcomes) == ["granted", "refused"]
    with unit_of_work() as uow:
        assert uow.ai_reservations.count_used(user_id) == CEILING


def test_reservations_of_different_users_do_not_wait_for_each_other(
    engine: Engine, unit_of_work: Callable
):
    quota = QuotaService(KIND, unit_of_work, TIERS)
    busy_user, other_user = uuid.uuid4(), uuid.uuid4()
    with engine.connect() as holder:
        holder.execute(HOLD_LOCK, {"user_id": busy_user})

        assert quota.reserve(uuid.uuid4(), other_user).created

        holder.rollback()


def test_a_reservation_that_cannot_get_the_lock_in_time_fails_with_lock_timeout(
    engine: Engine, unit_of_work: Callable
):
    user_id = uuid.uuid4()
    impatient = QuotaService(
        KIND, lambda: unit_of_work(lock_timeout=timedelta(milliseconds=200)), TIERS
    )
    with engine.connect() as holder:
        holder.execute(HOLD_LOCK, {"user_id": user_id})

        started = time.monotonic()
        with pytest.raises(LockTimeout):
            impatient.reserve(uuid.uuid4(), user_id)
        waited = time.monotonic() - started

        holder.rollback()

    assert 0.2 <= waited < WAIT_SECONDS
    assert impatient.reserve(uuid.uuid4(), user_id).created
