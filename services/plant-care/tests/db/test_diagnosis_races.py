import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.orm import Session

from app.models import DiagnosisModel
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.model import ModelEffort
from tests.db.factories import add_diagnosis, add_update

WAIT_SECONDS = 5
ROW_LOCK_WAITERS = text(
    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
    " AND wait_event_type = 'Lock' AND wait_event IN ('transactionid', 'tuple')"
)

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


def committed_diagnosis(engine: Engine, **overrides) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine) as session:
        update_id = add_update(session)["id"]
        reservation_id = add_diagnosis(session, update_id, **overrides)["ai_reservation_id"]
        session.commit()
    return update_id, reservation_id


def wait_until_an_attempt_waits_for_the_row(watcher: Connection) -> None:
    """Poll until a backend waits for a row lock; no fixed delay is involved."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if watcher.execute(ROW_LOCK_WAITERS).scalar_one():
            return
    raise AssertionError("no attempt ever waited for the diagnosis row")


def first_claim(unit_of_work: UnitOfWorkFactory, update_id: uuid.UUID, reservation_id: uuid.UUID):
    with unit_of_work() as uow:
        claimed = uow.diagnoses.claim_first_attempt(update_id, reservation_id, ModelEffort.HIGH)
        if claimed:
            uow.commit()
        return claimed


def retry_claim(
    unit_of_work: UnitOfWorkFactory, update_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID
):
    with unit_of_work() as uow:
        claimed = uow.diagnoses.claim_retry(update_id, old_id, new_id, ModelEffort.HIGH)
        if claimed:
            uow.commit()
        return claimed


def test_an_attempt_waits_for_the_one_holding_the_row_and_then_finds_it_claimed(
    engine: Engine, unit_of_work: UnitOfWorkFactory
):
    update_id, reservation_id = committed_diagnosis(engine)
    with (
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher,
        ThreadPoolExecutor(max_workers=1) as pool,
        unit_of_work() as winner,
    ):
        assert winner.diagnoses.claim_first_attempt(update_id, reservation_id, ModelEffort.HIGH)
        loser = pool.submit(first_claim, unit_of_work, update_id, reservation_id)
        wait_until_an_attempt_waits_for_the_row(watcher)
        winner.commit()

    assert loser.result(timeout=WAIT_SECONDS) is False


@pytest.mark.parametrize("round_number", range(5))
def test_two_parallel_first_attempts_claim_one_diagnosis_exactly_once(
    engine: Engine, unit_of_work: UnitOfWorkFactory, round_number: int
):
    update_id, reservation_id = committed_diagnosis(engine)
    start_together = Barrier(2)

    def contender() -> bool:
        start_together.wait(timeout=WAIT_SECONDS)
        return first_claim(unit_of_work, update_id, reservation_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        contenders = [pool.submit(contender) for _ in range(2)]
        outcomes = [future.result(timeout=WAIT_SECONDS) for future in contenders]

    assert sorted(outcomes) == [False, True]


@pytest.mark.parametrize("round_number", range(5))
def test_two_parallel_retries_of_a_degraded_diagnosis_leave_one_new_reservation(
    engine: Engine, unit_of_work: UnitOfWorkFactory, round_number: int
):
    update_id, old_id = committed_diagnosis(
        engine, status="degraded", model_effort="medium", recommendation="fallback"
    )
    new_ids = [uuid.uuid4(), uuid.uuid4()]
    start_together = Barrier(2)

    def contender(new_id: uuid.UUID) -> bool:
        start_together.wait(timeout=WAIT_SECONDS)
        return retry_claim(unit_of_work, update_id, old_id, new_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender, new_id) for new_id in new_ids]
        outcomes = [future.result(timeout=WAIT_SECONDS) for future in futures]

    with Session(engine) as session:
        reservation_id = session.scalar(
            select(DiagnosisModel.ai_reservation_id).where(DiagnosisModel.update_id == update_id)
        )
    assert sorted(outcomes) == [False, True]
    assert reservation_id == new_ids[outcomes.index(True)]


def test_a_result_written_after_a_rival_took_the_diagnosis_is_refused(
    engine: Engine, unit_of_work: UnitOfWorkFactory
):
    update_id, old_id = committed_diagnosis(
        engine, status="degraded", model_effort="medium", recommendation="fallback"
    )
    slow_attempt, rival = uuid.uuid4(), uuid.uuid4()
    assert retry_claim(unit_of_work, update_id, old_id, slow_attempt)
    assert retry_claim(unit_of_work, update_id, slow_attempt, rival)

    with unit_of_work() as uow:
        degraded = uow.diagnoses.mark_degraded(update_id, slow_attempt, "late fallback")

    assert degraded is False
