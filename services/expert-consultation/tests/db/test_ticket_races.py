import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.orm import Session

from app.models import AnswerModel
from app.repository.tickets import SqlAlchemyTicketRepository
from app.service.model import TicketStatus
from tests.db.factories import add_ticket

WAIT_SECONDS = 5
LOCK_TIMEOUT = text("SET LOCAL lock_timeout = '5s'")
ROW_LOCK_WAITERS = text(
    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
    " AND wait_event_type = 'Lock' AND wait_event IN ('transactionid', 'tuple')"
)


def repository(session: Session) -> SqlAlchemyTicketRepository:
    return SqlAlchemyTicketRepository(session)


def clock_of(session: Session) -> datetime:
    """Start the session's transaction and return its fixed `now()`."""
    return session.scalar(select(func.now()))


def commit_ticket_expiring_just_after(engine: Engine, instant: datetime) -> uuid.UUID:
    """A ticket that is still open for a transaction whose clock is `instant`, and overdue for any
    transaction that starts later: the window in which an answer races the expiry sweep."""
    with Session(engine) as seed:
        ticket = add_ticket(
            seed,
            created_at=instant - timedelta(hours=1),
            expires_at=instant + timedelta(microseconds=1),
        )
        seed.commit()
    return ticket["id"]


def wait_until_a_backend_waits_for_a_row(watcher: Connection) -> None:
    """Poll until a backend waits for a row lock; no fixed delay is involved."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if watcher.execute(ROW_LOCK_WAITERS).scalar_one():
            return
    raise AssertionError("no transaction ever waited for the ticket row")


def answer_in_its_own_transaction(
    engine: Engine, ticket_id: uuid.UUID, expert_id: uuid.UUID
) -> bool:
    with Session(engine) as session:
        session.execute(LOCK_TIMEOUT)
        answered = repository(session).answer(ticket_id, expert_id, "Water less.")
        if answered:
            session.commit()
        return answered


def final_state(engine: Engine, ticket_id: uuid.UUID) -> tuple[TicketStatus, int]:
    with Session(engine) as session:
        ticket = repository(session).get(ticket_id)
        answers = session.scalar(select(func.count()).select_from(AnswerModel))
    return ticket.status, answers


def test_an_answer_holding_the_row_makes_the_sweep_skip_it(engine: Engine):
    with Session(engine) as answering, Session(engine) as sweeping:
        instant = clock_of(answering)
        ticket_id = commit_ticket_expiring_just_after(engine, instant)
        assert repository(answering).answer(ticket_id, uuid.uuid4(), "Water less.")

        sweeping.execute(LOCK_TIMEOUT)
        swept_meanwhile = repository(sweeping).claim_expired(10)
        sweeping.rollback()
        answering.commit()

    with Session(engine) as late_sweep:
        swept_afterwards = repository(late_sweep).claim_expired(10)

    assert swept_meanwhile == []
    assert swept_afterwards == []
    assert final_state(engine, ticket_id) == (TicketStatus.ANSWERED, 1)


def test_a_sweep_holding_the_row_defeats_an_answer_that_started_before_the_deadline(
    engine: Engine,
):
    with (
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher,
        ThreadPoolExecutor(max_workers=1) as pool,
        Session(engine) as answering,
        Session(engine) as sweeping,
    ):
        answering.execute(LOCK_TIMEOUT)
        instant = clock_of(answering)
        ticket_id = commit_ticket_expiring_just_after(engine, instant)
        (claimed,) = repository(sweeping).claim_expired(10)
        late_answer = pool.submit(
            repository(answering).answer, ticket_id, uuid.uuid4(), "Too late."
        )
        wait_until_a_backend_waits_for_a_row(watcher)
        sweeping.commit()

        answered = late_answer.result(timeout=WAIT_SECONDS)

    assert claimed.id == ticket_id
    assert answered is False
    assert final_state(engine, ticket_id) == (TicketStatus.CANCELLED, 0)


def test_two_experts_answering_at_once_leave_exactly_one_answer(engine: Engine):
    with Session(engine) as seed:
        ticket_id = add_ticket(seed)["id"]
        seed.commit()
    first_expert, second_expert = uuid.uuid4(), uuid.uuid4()

    with (
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as watcher,
        ThreadPoolExecutor(max_workers=1) as pool,
        Session(engine) as winner,
    ):
        assert repository(winner).answer(ticket_id, first_expert, "First.")
        loser = pool.submit(answer_in_its_own_transaction, engine, ticket_id, second_expert)
        wait_until_a_backend_waits_for_a_row(watcher)
        winner.commit()

        assert loser.result(timeout=WAIT_SECONDS) is False

    with Session(engine) as session:
        stored = repository(session).get(ticket_id)
    assert stored.answer.expert_id == first_expert
    assert stored.answer.body == "First."
