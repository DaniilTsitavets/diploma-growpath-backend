import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import AnswerModel, TicketModel
from tests.db.factories import (
    add_answer,
    add_answered_ticket,
    add_cancelled_ticket,
    add_ticket,
    db_now,
    violated,
)

# `jsonb::text` renders {"k": "<n characters>"} as n + 9 octets.
JSON_TEXT_OVERHEAD = 9
JSONB_TEXT_LIMIT = 131072


def rejected_by(session: Session, constraint: str, add, *args, **fields):
    with violated(session) as violation:
        add(session, *args, **fields)

    assert violation.constraint == constraint


def context_of_text_size(octets: int) -> dict:
    return {"k": "x" * (octets - JSON_TEXT_OVERHEAD)}


def test_an_escalation_has_at_most_one_ticket(session: Session):
    escalation_id = add_ticket(session)["escalation_id"]

    rejected_by(session, "uq_tickets_escalation_id", add_ticket, escalation_id=escalation_id)


@pytest.mark.parametrize(
    "context", [[], "text", 7, None], ids=["array", "string", "number", "null"]
)
def test_the_context_must_be_a_json_object(session: Session, context):
    # `None` is stored as a JSON null here, not as SQL NULL, which a NOT NULL column refuses first.
    rejected_by(session, "ck_tickets_context", add_ticket, context=context)


def test_the_context_may_be_empty_and_is_capped_by_the_jsonb_text_size(session: Session):
    add_ticket(session, context={})
    add_ticket(session, context=context_of_text_size(JSONB_TEXT_LIMIT))

    rejected_by(
        session,
        "ck_tickets_context",
        add_ticket,
        context=context_of_text_size(JSONB_TEXT_LIMIT + 1),
    )


def hour(offset: int) -> timedelta:
    return timedelta(hours=offset)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "open", "answered_at": "now"},
        {"status": "open", "cancelled_at": "now"},
        {"status": "answered"},
        {"status": "answered", "answered_at": "now", "cancelled_at": "now"},
        {"status": "cancelled"},
        {"status": "cancelled", "cancelled_at": "later", "answered_at": "now"},
        {"status": "closed"},
        {"status": "closed", "answered_at": "now"},
    ],
    ids=[
        "open with an answer time",
        "open with a cancellation time",
        "answered without an answer time",
        "answered and cancelled",
        "cancelled without a cancellation time",
        "cancelled and answered",
        "unknown status",
        "unknown status with a time",
    ],
)
def test_the_state_columns_must_match_the_status(session: Session, fields: dict):
    now = db_now(session)
    times = {"now": now - hour(1), "later": now + hour(80)}
    resolved = {name: times.get(value, value) for name, value in fields.items()}

    rejected_by(
        session,
        "ck_tickets_state",
        add_ticket,
        created_at=now - hour(2),
        expires_at=now + hour(70),
        **resolved,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"expires_at": "created"},
        {"expires_at": "before_created"},
        {"status": "answered", "answered_at": "before_created"},
        {"status": "answered", "answered_at": "after_expiry"},
        {"status": "cancelled", "cancelled_at": "before_expiry"},
    ],
    ids=[
        "expires at creation",
        "expires before creation",
        "answered before creation",
        "answered after the deadline",
        "cancelled before the deadline",
    ],
)
def test_the_times_must_be_in_order(session: Session, fields: dict):
    now = db_now(session)
    created, expires = now - hour(10), now + hour(10)
    times = {
        "created": created,
        "before_created": created - hour(1),
        "after_expiry": expires + timedelta(microseconds=1),
        "before_expiry": expires - timedelta(microseconds=1),
    }
    resolved = {name: times.get(value, value) for name, value in fields.items()}

    rejected_by(
        session,
        "ck_tickets_times",
        add_ticket,
        **{"created_at": created, "expires_at": expires, **resolved},
    )


def test_the_times_may_touch_their_bounds(session: Session):
    now = db_now(session)
    created, expires = now - hour(10), now + hour(10)

    add_ticket(
        session, created_at=created, expires_at=expires, status="answered", answered_at=created
    )
    add_ticket(
        session, created_at=created, expires_at=expires, status="answered", answered_at=expires
    )
    add_ticket(
        session, created_at=created, expires_at=expires, status="cancelled", cancelled_at=expires
    )


def test_an_open_ticket_has_no_terminal_event(session: Session):
    rejected_by(
        session,
        "ck_tickets_event_published",
        add_ticket,
        terminal_event_published_at=db_now(session),
    )


def test_a_terminal_ticket_may_carry_the_published_flag(session: Session):
    add_answered_ticket(session, terminal_event_published_at=db_now(session))
    add_cancelled_ticket(session, terminal_event_published_at=db_now(session))


def test_only_one_open_ticket_per_update(session: Session):
    update_id = add_ticket(session)["update_id"]

    rejected_by(session, "uq_tickets_active_update", add_ticket, update_id=update_id)


def test_an_update_may_have_a_new_open_ticket_after_the_old_one_is_closed(session: Session):
    answered, _ = add_answered_ticket(session)
    add_cancelled_ticket(session, update_id=answered["update_id"])
    add_ticket(session, update_id=answered["update_id"])

    tickets = session.scalar(
        select(func.count()).where(TicketModel.update_id == answered["update_id"])
    )
    assert tickets == 3


def test_the_status_defaults_to_open_and_the_creation_time_to_the_database_clock(session: Session):
    session.add(
        TicketModel(
            id=uuid.uuid4(),
            escalation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            journal_id=uuid.uuid4(),
            update_id=uuid.uuid4(),
            context={},
            expires_at=db_now(session) + hour(72),
        )
    )
    session.flush()
    row = session.execute(select(TicketModel)).scalar_one()

    assert (row.status, row.created_at) == ("open", db_now(session))


@pytest.mark.parametrize("body", ["", " ", "\t", "\r\n", " \t\r\n "])
def test_an_answer_must_have_text(session: Session, body: str):
    ticket_id = add_ticket(session)["id"]

    rejected_by(session, "ck_answers_body", add_answer, ticket_id, body=body)


def test_an_answer_is_at_most_five_thousand_characters(session: Session):
    first, second, third = (add_ticket(session)["id"] for _ in range(3))

    add_answer(session, first, body="a" * 5000)
    add_answer(session, second, body="я" * 5000)
    rejected_by(session, "ck_answers_body", add_answer, third, body="a" * 5001)


def test_an_answer_may_be_padded_with_blanks_when_it_has_text(session: Session):
    add_answer(session, add_ticket(session)["id"], body="\n  Water less.\t\n")


def test_a_ticket_has_one_answer(session: Session):
    ticket_id = add_ticket(session)["id"]
    add_answer(session, ticket_id)

    rejected_by(session, "pk_answers", add_answer, ticket_id)


def test_an_answer_needs_an_existing_ticket(session: Session):
    rejected_by(session, "fk_answers_ticket_id_tickets", add_answer, uuid.uuid4())


def test_a_ticket_with_an_answer_cannot_be_deleted(session: Session):
    ticket, _ = add_answered_ticket(session)

    with violated(session) as violation:
        session.execute(delete(TicketModel).where(TicketModel.id == ticket["id"]))

    assert violation.constraint == "fk_answers_ticket_id_tickets"
    assert session.scalar(select(AnswerModel.ticket_id)) == ticket["id"]
