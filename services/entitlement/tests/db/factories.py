import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AiQuotaReservation, ExpertQuotaReservation, PlacedOrder

RESERVATION_MODELS = [AiQuotaReservation, ExpertQuotaReservation]


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


def db_now(session: Session) -> datetime:
    """`now()` of the open transaction, which stays fixed until it ends."""
    return session.scalar(select(func.now()))


def key_name(model: type[AiQuotaReservation] | type[ExpertQuotaReservation]) -> str:
    (column,) = model.__table__.primary_key.columns
    return column.name


def placed_order_values(**overrides) -> dict:
    fields = {
        "order_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "total_paid_cents": 2000,
        "placed_at": datetime.now(UTC),
    }
    return {**fields, **overrides}


def reservation_values(model, **overrides) -> dict:
    fields = {
        key_name(model): uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    return {**fields, **overrides}


def add_placed_order(session: Session, **overrides) -> dict:
    values = placed_order_values(**overrides)
    session.execute(insert(PlacedOrder).values(**values))
    return values


def add_reservation(session: Session, model, **overrides) -> dict:
    values = reservation_values(model, **overrides)
    session.execute(insert(model).values(**values))
    return values
