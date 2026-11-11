import itertools
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session

from app.models import EscalationModel
from app.repository.ticket_events import SqlAlchemyTicketEventRepository
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.ticket_events import TicketAnswered, TicketCancelled, TicketCreated
from app.service.ticket_handlers import TicketEventHandlers
from tests.db.factories import add_escalation, violated

ANSWERED_AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
TICKET = uuid.uuid4()


def repository(session: Session) -> SqlAlchemyTicketEventRepository:
    return SqlAlchemyTicketEventRepository(session)


def stored(session: Session, escalation_id: uuid.UUID) -> dict:
    row = session.execute(
        text("SELECT status, ticket_id, answer_text, answered_at FROM escalations WHERE id = :id"),
        {"id": escalation_id},
    ).one()
    return dict(row._mapping)


def test_ticket_created_opens_only_a_requested_escalation(session: Session):
    escalation_id = add_escalation(session)["id"]

    assert repository(session).mark_ticket_open(escalation_id, TICKET)
    assert not repository(session).mark_ticket_open(escalation_id, TICKET)

    assert stored(session, escalation_id)["status"] == "ticket_open"
    assert stored(session, escalation_id)["ticket_id"] == TICKET


def test_ticket_answered_works_from_requested_and_from_open(session: Session):
    early = add_escalation(session)["id"]
    opened = add_escalation(session)["id"]
    repository(session).mark_ticket_open(opened, uuid.uuid4())
    opened_ticket = stored(session, opened)["ticket_id"]

    assert repository(session).mark_answered(early, TICKET, "Early.", ANSWERED_AT)
    assert repository(session).mark_answered(opened, opened_ticket, "Later.", ANSWERED_AT)

    assert stored(session, early) == {
        "status": "answered",
        "ticket_id": TICKET,
        "answer_text": "Early.",
        "answered_at": ANSWERED_AT,
    }
    assert stored(session, opened)["answer_text"] == "Later."


def test_ticket_cancelled_works_from_requested_and_from_open(session: Session):
    requested = add_escalation(session)["id"]
    opened = add_escalation(session)["id"]
    repository(session).mark_ticket_open(opened, TICKET)

    assert repository(session).mark_cancelled(requested, uuid.uuid4())
    assert repository(session).mark_cancelled(opened, TICKET)

    assert stored(session, requested)["status"] == "cancelled"
    assert stored(session, opened)["status"] == "cancelled"


def test_nothing_leaves_a_terminal_state(session: Session):
    answered = add_escalation(session)["id"]
    cancelled = add_escalation(session)["id"]
    repository(session).mark_answered(answered, TICKET, "Done.", ANSWERED_AT)
    cancelled_ticket = uuid.uuid4()
    repository(session).mark_cancelled(cancelled, cancelled_ticket)

    repo = repository(session)
    assert not repo.mark_ticket_open(answered, TICKET)
    assert not repo.mark_cancelled(answered, TICKET)
    assert not repo.mark_answered(answered, TICKET, "Other.", ANSWERED_AT)
    assert not repo.mark_ticket_open(cancelled, cancelled_ticket)
    assert not repo.mark_answered(cancelled, cancelled_ticket, "Late.", ANSWERED_AT)

    assert stored(session, answered)["answer_text"] == "Done."
    assert stored(session, cancelled)["answer_text"] is None


def test_another_ticket_cannot_take_over_an_escalation_that_has_one(session: Session):
    escalation_id = add_escalation(session)["id"]
    repository(session).mark_ticket_open(escalation_id, TICKET)
    other = uuid.uuid4()

    assert not repository(session).mark_answered(escalation_id, other, "x", ANSWERED_AT)
    assert not repository(session).mark_cancelled(escalation_id, other)

    assert stored(session, escalation_id)["ticket_id"] == TICKET


def test_an_unknown_escalation_changes_nothing(session: Session):
    assert not repository(session).mark_ticket_open(uuid.uuid4(), TICKET)


def test_a_ticket_belongs_to_one_escalation(session: Session):
    first = add_escalation(session)["id"]
    second = add_escalation(session)["id"]
    repository(session).mark_ticket_open(first, TICKET)

    with violated(session) as violation:
        repository(session).mark_ticket_open(second, TICKET)

    assert violation.constraint == "uq_escalations_ticket_id"


def test_a_state_without_the_ticket_id_is_rejected_by_the_database(session: Session):
    escalation_id = add_escalation(session)["id"]

    with violated(session) as violation:
        session.execute(
            update(EscalationModel)
            .where(EscalationModel.id == escalation_id)
            .values(status="ticket_open")
        )

    assert violation.constraint == "ck_escalations_ticket_open"


def test_an_answer_without_its_parts_is_rejected_by_the_database(session: Session):
    escalation_id = add_escalation(session)["id"]

    with violated(session) as violation:
        session.execute(
            update(EscalationModel)
            .where(EscalationModel.id == escalation_id)
            .values(status="answered", ticket_id=TICKET, answer_text="x")
        )

    assert violation.constraint == "ck_escalations_answered"


@pytest.mark.parametrize(
    "order", list(itertools.permutations(["created", "answered", "cancelled"]))
)
def test_every_arrival_order_satisfies_the_checks_and_the_first_terminal_event_wins(
    engine: Engine, unit_of_work: Callable[[], SqlAlchemyUnitOfWork], order: tuple[str, ...]
):
    with Session(engine) as seed:
        escalation_id = add_escalation(seed)["id"]
        seed.commit()
    handlers = TicketEventHandlers(unit_of_work)
    events = {
        "created": lambda: handlers.ticket_created(TicketCreated(escalation_id, TICKET)),
        "answered": lambda: handlers.ticket_answered(
            TicketAnswered(escalation_id, TICKET, "Repot it.", ANSWERED_AT)
        ),
        "cancelled": lambda: handlers.ticket_cancelled(TicketCancelled(escalation_id, TICKET)),
    }

    for event in order:
        events[event]()
        events[event]()

    with Session(engine) as check:
        row = stored(check, escalation_id)
    first_terminal = next(e for e in order if e != "created")
    assert row["status"] == {"answered": "answered", "cancelled": "cancelled"}[first_terminal]
    assert row["ticket_id"] == TICKET
    assert (row["answer_text"] is not None) == (row["status"] == "answered")
