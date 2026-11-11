import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JournalModel
from app.repository.journals import SqlAlchemyJournalRepository
from app.service.model import NewJournal
from tests.db.factories import add_journal, db_now


def new_journal(**overrides) -> NewJournal:
    fields = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "order_id": uuid.uuid4(),
        "species": "Basil",
    }
    return NewJournal(**{**fields, **overrides})


def test_adding_a_journal_twice_for_one_order_keeps_the_first(session: Session):
    repository = SqlAlchemyJournalRepository(session)
    first = new_journal()

    added = repository.add(first)
    duplicate = repository.add(new_journal(order_id=first.order_id, species="Mint"))

    assert (added, duplicate) == (True, False)
    assert session.scalars(select(JournalModel.species)).all() == ["Basil"]


def test_a_new_journal_is_not_planted(session: Session):
    repository = SqlAlchemyJournalRepository(session)
    journal = new_journal()
    repository.add(journal)

    stored = repository.get_owned(journal.id, journal.user_id)

    assert stored is not None
    assert (stored.species, stored.planted_on, stored.order_id) == (
        "Basil",
        None,
        journal.order_id,
    )


def test_a_journal_of_another_user_is_not_found(session: Session):
    values = add_journal(session)

    assert SqlAlchemyJournalRepository(session).get_owned(values["id"], uuid.uuid4()) is None


def test_journals_of_a_user_come_newest_first(session: Session):
    user_id = uuid.uuid4()
    now = db_now(session)
    old = add_journal(session, user_id=user_id, created_at=now - timedelta(days=2))
    new = add_journal(session, user_id=user_id, created_at=now)
    add_journal(session)

    journals = SqlAlchemyJournalRepository(session).list_for_user(user_id)

    assert [journal.id for journal in journals] == [new["id"], old["id"]]


def test_planting_sets_the_date_and_can_be_changed(session: Session):
    repository = SqlAlchemyJournalRepository(session)
    values = add_journal(session)
    args = (values["id"], values["user_id"])

    first = repository.set_planted_on(*args, date(2026, 11, 1))
    second = repository.set_planted_on(*args, date(2026, 11, 3))

    assert (first.planted_on, second.planted_on) == (date(2026, 11, 1), date(2026, 11, 3))
    assert session.scalar(select(func.count()).select_from(JournalModel)) == 1


def test_planting_a_journal_of_another_user_changes_nothing(session: Session):
    values = add_journal(session)

    result = SqlAlchemyJournalRepository(session).set_planted_on(
        values["id"], uuid.uuid4(), date(2026, 11, 1)
    )

    assert result is None
    assert session.scalar(select(JournalModel.planted_on)) is None
