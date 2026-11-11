import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AnswerModel, TicketModel


@dataclass
class Violation:
    constraint: str | None = None


@contextmanager
def violated(session: Session) -> Iterator[Violation]:
    """Run the block in a savepoint and record which constraint the database reported."""
    violation = Violation()
    try:
        with session.begin_nested():
            yield violation
    except IntegrityError as error:
        violation.constraint = error.orig.diag.constraint_name
    else:
        raise AssertionError("the database accepted a row that should be rejected")


def db_now(session: Session):
    """`now()` of the open transaction, which stays fixed until it ends."""
    return session.scalar(select(func.now()))


def _insert(session: Session, model, values: dict, overrides: dict) -> dict:
    row = {**values, **overrides}
    session.execute(insert(model).values(**row))
    return row


def add_ticket(session: Session, **overrides) -> dict:
    """An open ticket created an hour ago, with most of its 72 hours left."""
    now = db_now(session)
    values = {
        "id": uuid.uuid4(),
        "escalation_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "update_id": uuid.uuid4(),
        "context": {"schema_version": 1, "species": "Basil"},
        "created_at": now - timedelta(hours=1),
        "expires_at": now + timedelta(hours=71),
    }
    return _insert(session, TicketModel, values, overrides)


def add_overdue_ticket(session: Session, **overrides) -> dict:
    """An open ticket whose deadline passed eight hours ago."""
    now = db_now(session)
    deadline = {"created_at": now - timedelta(hours=80), "expires_at": now - timedelta(hours=8)}
    return add_ticket(session, **{**deadline, **overrides})


def add_answer(session: Session, ticket_id: uuid.UUID, **overrides) -> dict:
    values = {"ticket_id": ticket_id, "expert_id": uuid.uuid4(), "body": "Water less often."}
    return _insert(session, AnswerModel, values, overrides)


def add_answered_ticket(
    session: Session, answer: dict | None = None, **overrides
) -> tuple[dict, dict]:
    """An answered ticket and its answer row."""
    ticket = add_ticket(
        session,
        **{
            "status": "answered",
            "answered_at": db_now(session) - timedelta(minutes=30),
            **overrides,
        },
    )
    return ticket, add_answer(session, ticket["id"], **(answer or {}))


def add_cancelled_ticket(session: Session, **overrides) -> dict:
    """A ticket that expired eight hours ago and was cancelled one hour ago."""
    fields = {"status": "cancelled", "cancelled_at": db_now(session) - timedelta(hours=1)}
    return add_overdue_ticket(session, **{**fields, **overrides})
