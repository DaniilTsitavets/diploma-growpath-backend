import uuid
from datetime import date

import pytest

from app.service.checkpoints import CheckpointState
from app.service.errors import JournalNotFound
from app.service.journals import JournalService
from tests.unit.fakes import TODAY, FakeUnitOfWork

USER = uuid.uuid4()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def service(uow: FakeUnitOfWork) -> JournalService:
    return JournalService(lambda: uow, lambda: TODAY)


def test_journals_of_the_user_come_newest_first(service: JournalService, uow: FakeUnitOfWork):
    old = uow.journals.seed(USER)
    new = uow.journals.seed(USER)
    uow.journals.seed(uuid.uuid4())

    views = service.list_for_user(USER)

    assert [view.journal.id for view in views] == [new.id, old.id]


def test_an_unplanted_journal_has_no_checkpoints(service: JournalService, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)

    assert service.get(journal.id, USER).checkpoints == []


def test_checkpoints_are_computed_from_the_planting_date_and_today(
    service: JournalService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER, planted_on=date(2026, 11, 1))

    checkpoints = service.get(journal.id, USER).checkpoints

    assert [c.state for c in checkpoints] == [
        CheckpointState.PASSED,
        CheckpointState.OPEN,
        CheckpointState.UPCOMING,
    ]


def test_a_journal_of_another_user_is_not_found(service: JournalService, uow: FakeUnitOfWork):
    journal = uow.journals.seed(uuid.uuid4())

    with pytest.raises(JournalNotFound):
        service.get(journal.id, USER)


def test_planting_stores_the_date_and_commits(service: JournalService, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)

    view = service.plant(journal.id, USER, date(2026, 11, 2))

    assert view.journal.planted_on == date(2026, 11, 2)
    assert uow.journals.rows[journal.id].planted_on == date(2026, 11, 2)
    assert uow.commits == 1
    assert len(view.checkpoints) == 3


def test_planting_a_journal_of_another_user_is_not_found(
    service: JournalService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(uuid.uuid4())

    with pytest.raises(JournalNotFound):
        service.plant(journal.id, USER, date(2026, 11, 2))

    assert uow.journals.rows[journal.id].planted_on is None
