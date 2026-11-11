import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import EscalationModel
from app.repository.escalations import SqlAlchemyEscalationRepository
from app.service.model import EscalationStatus, NewEscalation
from tests.db.factories import add_escalation, add_journal, add_update, db_now


def repository(session: Session) -> SqlAlchemyEscalationRepository:
    return SqlAlchemyEscalationRepository(session)


def test_add_stores_a_requested_escalation_with_the_database_time(session: Session):
    update_id = add_update(session)["id"]
    escalation = NewEscalation(uuid.uuid4(), update_id, "Why the spots?")

    assert repository(session).add(escalation)

    stored = repository(session).get(escalation.id)
    assert stored is not None
    assert stored.status is EscalationStatus.REQUESTED
    assert (stored.update_id, stored.user_question) == (update_id, "Why the spots?")
    assert (stored.ticket_id, stored.answer_text, stored.answered_at) == (None, None, None)
    assert stored.created_at == db_now(session)


def test_add_reports_the_journal_and_the_user_of_the_update(session: Session):
    journal = add_journal(session)
    update_id = add_update(session, journal["id"])["id"]
    escalation = NewEscalation(uuid.uuid4(), update_id, None)

    repository(session).add(escalation)

    stored = repository(session).get(escalation.id)
    assert stored is not None
    assert (stored.journal_id, stored.user_id) == (journal["id"], journal["user_id"])


def test_a_second_active_escalation_is_skipped_by_on_conflict(session: Session):
    update_id = add_update(session)["id"]
    first = NewEscalation(uuid.uuid4(), update_id, None)
    second = NewEscalation(uuid.uuid4(), update_id, "other")

    assert repository(session).add(first)
    assert not repository(session).add(second)

    assert repository(session).get(second.id) is None
    active = repository(session).get_active(update_id)
    assert active is not None
    assert active.id == first.id


def test_an_escalation_with_a_ticket_still_blocks_a_new_one(session: Session):
    update_id = add_update(session)["id"]
    add_escalation(
        session,
        update_id,
        status="answered",
        ticket_id=uuid.uuid4(),
        answer_text="Done",
        answered_at=db_now(session),
    )

    assert not repository(session).add(NewEscalation(uuid.uuid4(), update_id, None))


def test_a_cancelled_escalation_allows_a_new_one_and_stays_findable_as_latest(session: Session):
    update_id = add_update(session)["id"]
    cancelled = add_escalation(session, update_id, created_at=db_now(session) - timedelta(days=1))
    session.execute(
        update(EscalationModel)
        .where(EscalationModel.id == cancelled["id"])
        .values(status="cancelled")
    )
    assert repository(session).get_active(update_id) is None

    fresh = NewEscalation(uuid.uuid4(), update_id, None)
    assert repository(session).add(fresh)

    latest = repository(session).latest(update_id)
    assert latest is not None
    assert latest.id == fresh.id
    old_status = select(EscalationModel.status).where(EscalationModel.id == cancelled["id"])
    assert session.scalar(old_status) == "cancelled"


def test_lookups_of_unknown_ids_return_none(session: Session):
    assert repository(session).get(uuid.uuid4()) is None
    assert repository(session).get_active(uuid.uuid4()) is None
    assert repository(session).latest(uuid.uuid4()) is None
