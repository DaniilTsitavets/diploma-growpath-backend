import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnswerModel, TicketModel
from app.repository.tickets import SqlAlchemyTicketRepository
from app.service.model import NewTicket, QueueCursor, TicketStatus
from tests.db.factories import (
    add_answered_ticket,
    add_cancelled_ticket,
    add_overdue_ticket,
    add_ticket,
    db_now,
    violated,
)

TTL_SECONDS = 259200
GRACE_SECONDS = 30.0
CONTEXT = {
    "schema_version": 1,
    "species": "Мята",
    "photos": [{"kind": "overview", "storage_key": "k"}],
}


def repository(session: Session) -> SqlAlchemyTicketRepository:
    return SqlAlchemyTicketRepository(session)


def new_ticket(**overrides) -> NewTicket:
    fields = {
        "id": uuid.uuid4(),
        "escalation_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "update_id": uuid.uuid4(),
        "context": CONTEXT,
    }
    return NewTicket(**{**fields, **overrides})


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_create_stores_an_open_ticket_that_expires_ttl_after_the_database_clock(session: Session):
    ticket = new_ticket()

    created = repository(session).create(ticket, TTL_SECONDS, db_now(session) + timedelta(hours=1))

    stored = repository(session).get(ticket.id)
    assert created is True
    assert stored.status is TicketStatus.OPEN
    assert stored.created_at == db_now(session)
    assert stored.expires_at - stored.created_at == timedelta(seconds=TTL_SECONDS)
    assert (stored.escalation_id, stored.user_id, stored.journal_id, stored.update_id) == (
        ticket.escalation_id,
        ticket.user_id,
        ticket.journal_id,
        ticket.update_id,
    )
    assert stored.context == CONTEXT
    assert (stored.answered_at, stored.cancelled_at, stored.answer) == (None, None, None)
    assert stored.terminal_event_published_at is None


def test_create_refuses_an_event_whose_acceptance_window_has_passed(session: Session):
    ticket = new_ticket()

    created = repository(session).create(
        ticket, TTL_SECONDS, db_now(session) - timedelta(seconds=1)
    )

    assert created is False
    assert count(session, TicketModel) == 0


def test_the_acceptance_window_closes_exactly_at_accept_until(session: Session):
    assert repository(session).create(new_ticket(), TTL_SECONDS, db_now(session)) is False


def test_create_ignores_an_escalation_that_already_has_a_ticket(session: Session):
    first = new_ticket()
    repository(session).create(first, TTL_SECONDS, db_now(session) + timedelta(hours=1))

    created = repository(session).create(
        new_ticket(escalation_id=first.escalation_id),
        TTL_SECONDS,
        db_now(session) + timedelta(hours=1),
    )

    assert created is False
    assert count(session, TicketModel) == 1
    assert repository(session).get_by_escalation(first.escalation_id).id == first.id


def test_a_second_open_ticket_for_the_same_update_breaks_the_invariant(session: Session):
    first = new_ticket()
    repository(session).create(first, TTL_SECONDS, db_now(session) + timedelta(hours=1))

    with violated(session) as violation:
        repository(session).create(
            new_ticket(update_id=first.update_id), TTL_SECONDS, db_now(session) + timedelta(hours=1)
        )

    assert violation.constraint == "uq_tickets_active_update"


def test_get_joins_the_answer(session: Session):
    ticket, answer = add_answered_ticket(session, answer={"body": "Repot it."})

    stored = repository(session).get(ticket["id"])

    assert stored.status is TicketStatus.ANSWERED
    assert (stored.answer.expert_id, stored.answer.body) == (answer["expert_id"], "Repot it.")
    assert stored.answered_at == ticket["answered_at"]


def test_unknown_tickets_are_not_found(session: Session):
    assert repository(session).get(uuid.uuid4()) is None
    assert repository(session).get_by_escalation(uuid.uuid4()) is None


def test_the_queue_lists_open_unexpired_tickets_soonest_deadline_first(session: Session):
    now = db_now(session)
    later = add_ticket(session, expires_at=now + timedelta(hours=10))
    sooner = add_ticket(session, expires_at=now + timedelta(hours=2), context={"species": "Mint"})
    add_overdue_ticket(session)
    add_answered_ticket(session)
    add_cancelled_ticket(session)

    queue = repository(session).open_queue(10, None)

    assert [item.id for item in queue] == [sooner["id"], later["id"]]
    assert [item.species for item in queue] == ["Mint", "Basil"]
    assert queue[0].expires_at == sooner["expires_at"]
    assert queue[0].created_at == sooner["created_at"]


def test_a_ticket_whose_deadline_is_this_instant_has_left_the_queue(session: Session):
    now = db_now(session)
    add_ticket(session, created_at=now - timedelta(hours=1), expires_at=now)

    assert repository(session).open_queue(10, None) == []


def test_the_queue_pages_by_deadline_and_breaks_ties_by_id(session: Session):
    deadline = db_now(session) + timedelta(hours=5)
    tied = sorted(
        (add_ticket(session, expires_at=deadline)["id"] for _ in range(3)), key=lambda i: i
    )
    last = add_ticket(session, expires_at=deadline + timedelta(hours=1))["id"]

    first_page = repository(session).open_queue(2, None)
    cursor = QueueCursor(first_page[-1].expires_at, first_page[-1].id)
    second_page = repository(session).open_queue(2, cursor)
    third_page = repository(session).open_queue(2, QueueCursor(deadline + timedelta(hours=1), last))

    assert [item.id for item in first_page] == tied[:2]
    assert [item.id for item in second_page] == [tied[2], last]
    assert third_page == []


def test_a_ticket_without_a_species_is_listed_with_none(session: Session):
    add_ticket(session, context={"schema_version": 1})

    (item,) = repository(session).open_queue(10, None)

    assert item.species is None


def test_an_answer_closes_the_ticket_and_stores_the_text(session: Session):
    ticket = add_ticket(session)
    expert_id = uuid.uuid4()

    answered = repository(session).answer(ticket["id"], expert_id, "Water less.")

    stored = repository(session).get(ticket["id"])
    assert answered is True
    assert stored.status is TicketStatus.ANSWERED
    assert stored.answered_at == db_now(session)
    assert (stored.answer.expert_id, stored.answer.body) == (expert_id, "Water less.")


def test_an_answer_is_refused_unless_the_ticket_is_open_and_in_time(session: Session):
    answered, _ = add_answered_ticket(session)
    cancelled = add_cancelled_ticket(session)
    overdue = add_overdue_ticket(session)

    for ticket_id in (answered["id"], cancelled["id"], overdue["id"], uuid.uuid4()):
        assert repository(session).answer(ticket_id, uuid.uuid4(), "Too late.") is False

    assert count(session, AnswerModel) == 1
    assert repository(session).get(overdue["id"]).status is TicketStatus.OPEN


def test_an_answer_at_the_deadline_instant_is_refused(session: Session):
    now = db_now(session)
    ticket = add_ticket(session, created_at=now - timedelta(hours=1), expires_at=now)

    assert repository(session).answer(ticket["id"], uuid.uuid4(), "x") is False


def test_the_expiry_claim_cancels_overdue_open_tickets_oldest_deadline_first(session: Session):
    now = db_now(session)
    newer = add_overdue_ticket(session, expires_at=now - timedelta(hours=1))
    older = add_overdue_ticket(session, expires_at=now - timedelta(hours=3))
    add_ticket(session)
    add_answered_ticket(session)
    add_cancelled_ticket(session)

    claimed = repository(session).claim_expired(10)

    assert [ticket.id for ticket in claimed] == [older["id"], newer["id"]]
    assert all(ticket.status is TicketStatus.CANCELLED for ticket in claimed)
    assert all(ticket.cancelled_at == now for ticket in claimed)
    assert all(ticket.terminal_event_published_at is None for ticket in claimed)
    assert repository(session).claim_expired(10) == []


def test_a_ticket_whose_deadline_is_this_instant_is_expired(session: Session):
    now = db_now(session)
    ticket = add_ticket(session, created_at=now - timedelta(hours=1), expires_at=now)

    (claimed,) = repository(session).claim_expired(10)

    assert claimed.id == ticket["id"]


def test_the_expiry_claim_respects_the_batch_size(session: Session):
    now = db_now(session)
    tickets = [
        add_overdue_ticket(session, expires_at=now - timedelta(hours=hours)) for hours in (3, 2, 1)
    ]

    claimed = repository(session).claim_expired(2)

    assert [ticket.id for ticket in claimed] == [tickets[0]["id"], tickets[1]["id"]]
    assert repository(session).get(tickets[2]["id"]).status is TicketStatus.OPEN


def test_the_republisher_claim_takes_unpublished_terminal_tickets_after_the_grace(session: Session):
    now = db_now(session)
    answered, answer = add_answered_ticket(session, answered_at=now - timedelta(minutes=5))
    cancelled = add_cancelled_ticket(session, cancelled_at=now - timedelta(minutes=5))
    add_answered_ticket(session, answered_at=now - timedelta(seconds=10))
    add_answered_ticket(
        session,
        answered_at=now - timedelta(minutes=5),
        terminal_event_published_at=now - timedelta(minutes=4),
    )
    add_ticket(session)
    add_overdue_ticket(session)

    claimed = repository(session).claim_unpublished(GRACE_SECONDS, 10)

    assert [ticket.id for ticket in claimed] == sorted([answered["id"], cancelled["id"]])
    by_id = {ticket.id: ticket for ticket in claimed}
    assert by_id[answered["id"]].answer.body == answer["body"]
    assert by_id[cancelled["id"]].answer is None


def test_the_republisher_claim_respects_the_batch_size(session: Session):
    now = db_now(session)
    for _ in range(3):
        add_cancelled_ticket(session, cancelled_at=now - timedelta(minutes=5))

    assert len(repository(session).claim_unpublished(GRACE_SECONDS, 2)) == 2


def test_marking_published_records_the_clock_once(session: Session):
    ticket = add_cancelled_ticket(session)

    assert repository(session).mark_published(ticket["id"]) is True
    assert repository(session).get(ticket["id"]).terminal_event_published_at == db_now(session)
    assert repository(session).mark_published(ticket["id"]) is False
    assert repository(session).mark_published(uuid.uuid4()) is False


def test_a_marked_ticket_leaves_the_republisher_claim(session: Session):
    ticket = add_cancelled_ticket(session, cancelled_at=db_now(session) - timedelta(minutes=5))
    repository(session).mark_published(ticket["id"])

    assert repository(session).claim_unpublished(GRACE_SECONDS, 10) == []
