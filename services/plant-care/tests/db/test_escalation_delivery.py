import uuid
from datetime import timedelta

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.repository.escalation_delivery import SqlAlchemyEscalationDeliveryRepository
from app.service.model import DiagnosisStatus
from tests.db.factories import (
    add_candidate,
    add_diagnosis,
    add_escalation,
    add_journal,
    add_update,
    db_now,
)

GRACE = timedelta(seconds=30)
TTL = timedelta(hours=24)


def repository(session: Session) -> SqlAlchemyEscalationDeliveryRepository:
    return SqlAlchemyEscalationDeliveryRepository(session)


def escalate(session: Session, age: timedelta, **overrides) -> uuid.UUID:
    """An escalation of its own update, created `age` before the database clock."""
    row = add_escalation(session, created_at=db_now(session) - age, **overrides)
    return row["id"]


def test_claim_due_takes_requested_rows_between_the_grace_and_the_ttl_oldest_first(
    session: Session,
):
    newer = escalate(session, timedelta(minutes=5))
    older = escalate(session, timedelta(hours=3))
    escalate(session, timedelta(seconds=5))
    escalate(session, TTL)
    escalate(session, timedelta(hours=1), status="ticket_open", ticket_id=uuid.uuid4())
    escalate(session, timedelta(hours=1), status="cancelled")

    claimed = repository(session).claim_due(GRACE, TTL, limit=10)

    assert [e.id for e in claimed] == [older, newer]


def test_claim_due_reports_the_journal_and_the_user(session: Session):
    journal = add_journal(session)
    update_id = add_update(session, journal["id"])["id"]
    row = add_escalation(
        session, update_id, created_at=db_now(session) - timedelta(minutes=5), user_question="q"
    )

    (claimed,) = repository(session).claim_due(GRACE, TTL, limit=10)

    assert (claimed.id, claimed.journal_id, claimed.user_id) == (
        row["id"],
        journal["id"],
        journal["user_id"],
    )
    assert claimed.user_question == "q"


def test_claim_due_respects_the_batch_size(session: Session):
    ids = [escalate(session, timedelta(minutes=10 - i)) for i in range(3)]

    claimed = repository(session).claim_due(GRACE, TTL, limit=2)

    assert [e.id for e in claimed] == ids[:2]


def test_claim_expired_takes_requested_rows_at_or_past_the_ttl(session: Session):
    expired = escalate(session, TTL)
    older = escalate(session, TTL + timedelta(hours=1))
    escalate(session, TTL - timedelta(minutes=1))
    escalate(
        session,
        TTL + timedelta(hours=2),
        status="answered",
        ticket_id=uuid.uuid4(),
        answer_text="a",
        answered_at=db_now(session),
    )

    claimed = repository(session).claim_expired(TTL, limit=10)

    assert [e.id for e in claimed] == [older, expired]


def test_cancel_requested_is_guarded_by_the_status(session: Session):
    requested = escalate(session, TTL)
    opened = escalate(session, TTL, status="ticket_open", ticket_id=uuid.uuid4())

    assert repository(session).cancel_requested(requested)
    assert not repository(session).cancel_requested(requested)
    assert not repository(session).cancel_requested(opened)
    assert not repository(session).cancel_requested(uuid.uuid4())

    status = session.scalar(text("SELECT status FROM escalations WHERE id = :id"), {"id": opened})
    assert status == "ticket_open"


def test_a_cancelled_escalation_leaves_the_update_free_for_a_new_one(session: Session):
    update_id = add_update(session)["id"]
    first = add_escalation(session, update_id)["id"]

    assert repository(session).cancel_requested(first)

    add_escalation(session, update_id)


def test_history_lists_earlier_updates_newest_first_with_outcome_and_top_cause(session: Session):
    journal_id = add_journal(session)["id"]
    now = db_now(session)
    oldest = add_update(session, journal_id, created_at=now - timedelta(days=3))["id"]
    add_update(session, journal_id, created_at=now - timedelta(days=2))
    add_update(session, journal_id, created_at=now - timedelta(days=1))
    add_update(session, created_at=now - timedelta(days=2))
    add_diagnosis(
        session,
        oldest,
        status="completed",
        model_effort="high",
        has_dominant_candidate=True,
        recommendation="Water less.",
    )
    add_candidate(session, oldest, rank=1, cause="Overwatering")
    add_candidate(session, oldest, rank=2, cause="Pests")

    history = repository(session).history(journal_id, now - timedelta(hours=36), limit=5)

    assert [(h.created_at, h.diagnosis_status, h.top_cause) for h in history] == [
        (now - timedelta(days=2), None, None),
        (now - timedelta(days=3), DiagnosisStatus.COMPLETED, "Overwatering"),
    ]


def test_history_is_limited_to_the_newest_entries(session: Session):
    journal_id = add_journal(session)["id"]
    now = db_now(session)
    for day in (5, 4, 3):
        add_update(session, journal_id, created_at=now - timedelta(days=day))

    history = repository(session).history(journal_id, now, limit=2)

    assert [h.created_at for h in history] == [now - timedelta(days=3), now - timedelta(days=4)]


def test_a_second_worker_skips_the_rows_the_first_one_holds(engine: Engine):
    with Session(engine) as seed:
        older = escalate(seed, timedelta(minutes=10))
        newer = escalate(seed, timedelta(minutes=9))
        seed.commit()

    with Session(engine) as first, Session(engine) as second:
        first_claim = repository(first).claim_due(GRACE, TTL, limit=1)
        # A regression to plain FOR UPDATE would wait on the first worker; fail instead of hanging.
        second.execute(text("SET LOCAL lock_timeout = '2s'"))
        second_claim = repository(second).claim_due(GRACE, TTL, limit=10)

    assert [e.id for e in first_claim] == [older]
    assert [e.id for e in second_claim] == [newer]


def test_the_expiry_claim_also_skips_locked_rows(engine: Engine):
    with Session(engine) as seed:
        locked = escalate(seed, TTL + timedelta(hours=1))
        free = escalate(seed, TTL + timedelta(minutes=1))
        seed.commit()

    with Session(engine) as first, Session(engine) as second:
        first_claim = repository(first).claim_expired(TTL, limit=1)
        second.execute(text("SET LOCAL lock_timeout = '2s'"))
        second_claim = repository(second).claim_expired(TTL, limit=10)

    assert [e.id for e in first_claim] == [locked]
    assert [e.id for e in second_claim] == [free]


def test_the_claim_does_not_lock_the_update_or_journal_rows(engine: Engine):
    with Session(engine) as seed:
        escalation_id = escalate(seed, timedelta(minutes=5))
        seed.commit()

    with Session(engine) as claimer, Session(engine) as other:
        claimed = repository(claimer).claim_due(GRACE, TTL, limit=10)
        other.execute(text("SET LOCAL lock_timeout = '2s'"))
        other.execute(text("UPDATE updates SET lighting = 'low'"))
        other.execute(text("UPDATE journals SET species = 'Mint'"))

    assert [e.id for e in claimed] == [escalation_id]
