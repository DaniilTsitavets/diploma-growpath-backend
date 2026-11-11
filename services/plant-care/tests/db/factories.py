import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    DiagnosisCandidateModel,
    DiagnosisModel,
    EscalationModel,
    JournalModel,
    UpdateModel,
    UpdatePhotoModel,
)


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


def add_journal(session: Session, **overrides) -> dict:
    values = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "order_id": uuid.uuid4(),
        "species": "Basil",
    }
    return _insert(session, JournalModel, values, overrides)


def add_update(session: Session, journal_id: uuid.UUID | None = None, **overrides) -> dict:
    values = {
        "id": uuid.uuid4(),
        "journal_id": journal_id or add_journal(session)["id"],
        "drainage": "good",
        "lighting": "bright_indirect",
        "finger_test": "dry",
        "pot_weight": "lighter",
    }
    return _insert(session, UpdateModel, values, overrides)


def add_photo(session: Session, update_id: uuid.UUID | None = None, **overrides) -> dict:
    values = {
        "update_id": update_id or add_update(session)["id"],
        "kind": "overview",
        "storage_key": "journals/j/updates/u/overview",
    }
    return _insert(session, UpdatePhotoModel, values, overrides)


def add_diagnosis(session: Session, update_id: uuid.UUID | None = None, **overrides) -> dict:
    values = {
        "update_id": update_id or add_update(session)["id"],
        "ai_reservation_id": uuid.uuid4(),
        "status": "diagnosis_pending",
    }
    return _insert(session, DiagnosisModel, values, overrides)


def add_candidate(session: Session, update_id: uuid.UUID, **overrides) -> dict:
    values = {"update_id": update_id, "rank": 1, "cause": "Overwatering", "rationale": "Wet soil"}
    return _insert(session, DiagnosisCandidateModel, values, overrides)


def add_escalation(session: Session, update_id: uuid.UUID | None = None, **overrides) -> dict:
    values = {"id": uuid.uuid4(), "update_id": update_id or add_update(session)["id"]}
    return _insert(session, EscalationModel, values, overrides)
