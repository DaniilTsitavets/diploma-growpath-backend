import uuid
from datetime import date, timedelta

import pytest

from app.service.checkpoints import CheckpointState
from app.service.errors import (
    InvalidCursor,
    JournalNotFound,
    PlantingAlreadyRecorded,
    PlantingDateInFuture,
)
from app.service.journals import JournalService
from tests.unit.fakes import TODAY, FakeUnitOfWork

USER = uuid.uuid4()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def service(uow: FakeUnitOfWork) -> JournalService:
    return JournalService(lambda: uow, 2, 3, lambda: TODAY)


def test_journals_of_the_user_come_newest_first(service: JournalService, uow: FakeUnitOfWork):
    old = uow.journals.seed(USER)
    new = uow.journals.seed(USER)
    uow.journals.seed(uuid.uuid4())

    page = service.page_for_user(USER, None, None)

    assert [view.journal.id for view in page.items] == [new.id, old.id]
    assert page.next_cursor is None


def test_a_page_ends_with_a_cursor_that_continues_after_it(
    service: JournalService, uow: FakeUnitOfWork
):
    seeded = [uow.journals.seed(USER) for _ in range(3)]

    first = service.page_for_user(USER, 2, None)
    second = service.page_for_user(USER, 2, first.next_cursor)

    assert [view.journal.id for view in first.items] == [seeded[2].id, seeded[1].id]
    assert first.next_cursor is not None
    assert [view.journal.id for view in second.items] == [seeded[0].id]
    assert second.next_cursor is None


def test_the_page_size_is_clamped_to_the_maximum(service: JournalService, uow: FakeUnitOfWork):
    for _ in range(5):
        uow.journals.seed(USER)

    assert len(service.page_for_user(USER, 100, None).items) == 3


def test_a_malformed_cursor_is_rejected(service: JournalService):
    with pytest.raises(InvalidCursor):
        service.page_for_user(USER, None, "garbage")


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


def test_planting_today_is_allowed(service: JournalService, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)

    assert service.plant(journal.id, USER, TODAY).journal.planted_on == TODAY


def test_a_future_planting_date_is_rejected_without_a_write(
    service: JournalService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER)

    with pytest.raises(PlantingDateInFuture):
        service.plant(journal.id, USER, TODAY + timedelta(days=1))

    assert uow.journals.rows[journal.id].planted_on is None
    assert uow.commits == 0


def test_repeating_the_same_planting_date_is_idempotent(
    service: JournalService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER, planted_on=date(2026, 11, 2))

    view = service.plant(journal.id, USER, date(2026, 11, 2))

    assert view.journal.planted_on == date(2026, 11, 2)


def test_a_different_planting_date_is_a_conflict(service: JournalService, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER, planted_on=date(2026, 11, 2))

    with pytest.raises(PlantingAlreadyRecorded):
        service.plant(journal.id, USER, date(2026, 11, 3))

    assert uow.journals.rows[journal.id].planted_on == date(2026, 11, 2)
    assert uow.commits == 0


def test_planting_a_journal_of_another_user_is_not_found(
    service: JournalService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(uuid.uuid4())

    with pytest.raises(JournalNotFound):
        service.plant(journal.id, USER, date(2026, 11, 2))

    assert uow.journals.rows[journal.id].planted_on is None
