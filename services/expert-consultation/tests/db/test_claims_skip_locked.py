from datetime import timedelta

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.repository.tickets import SqlAlchemyTicketRepository
from tests.db.factories import (
    add_answered_ticket,
    add_cancelled_ticket,
    add_overdue_ticket,
    db_now,
)

GRACE_SECONDS = 30.0
# A regression to plain FOR UPDATE would wait on the first worker; fail instead of hanging.
LOCK_TIMEOUT = text("SET LOCAL lock_timeout = '2s'")


def repository(session: Session) -> SqlAlchemyTicketRepository:
    return SqlAlchemyTicketRepository(session)


def test_a_second_sweep_skips_the_tickets_the_first_one_holds(engine: Engine):
    with Session(engine) as seed:
        now = db_now(seed)
        older = add_overdue_ticket(seed, expires_at=now - timedelta(hours=3))["id"]
        newer = add_overdue_ticket(seed, expires_at=now - timedelta(hours=2))["id"]
        seed.commit()

    with Session(engine) as first, Session(engine) as second:
        first_claim = repository(first).claim_expired(1)
        second.execute(LOCK_TIMEOUT)
        second_claim = repository(second).claim_expired(10)

    assert [ticket.id for ticket in first_claim] == [older]
    assert [ticket.id for ticket in second_claim] == [newer]


def test_a_released_sweep_claim_leaves_the_tickets_open_for_the_next_one(engine: Engine):
    with Session(engine) as seed:
        ticket_id = add_overdue_ticket(seed)["id"]
        seed.commit()

    with Session(engine) as crashed:
        assert len(repository(crashed).claim_expired(10)) == 1
        crashed.rollback()

    with Session(engine) as next_run:
        assert [ticket.id for ticket in repository(next_run).claim_expired(10)] == [ticket_id]


def test_a_second_republisher_skips_the_tickets_the_first_one_holds(engine: Engine):
    with Session(engine) as seed:
        long_ago = db_now(seed) - timedelta(minutes=10)
        first_id, second_id = sorted(
            add_cancelled_ticket(seed, cancelled_at=long_ago)["id"] for _ in range(2)
        )
        seed.commit()

    with Session(engine) as first, Session(engine) as second:
        first_claim = repository(first).claim_unpublished(GRACE_SECONDS, 1)
        second.execute(LOCK_TIMEOUT)
        second_claim = repository(second).claim_unpublished(GRACE_SECONDS, 10)

    assert [ticket.id for ticket in first_claim] == [first_id]
    assert [ticket.id for ticket in second_claim] == [second_id]


def test_the_republisher_claim_does_not_lock_the_answers(engine: Engine):
    with Session(engine) as seed:
        ticket, _ = add_answered_ticket(seed, answered_at=db_now(seed) - timedelta(minutes=10))
        seed.commit()

    with Session(engine) as claimer, Session(engine) as other:
        claimed = repository(claimer).claim_unpublished(GRACE_SECONDS, 10)
        other.execute(LOCK_TIMEOUT)
        other.execute(text("UPDATE answers SET body = 'edited'"))

    assert [claimed_ticket.id for claimed_ticket in claimed] == [ticket["id"]]
