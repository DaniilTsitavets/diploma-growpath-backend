import uuid

import pytest

from app.service.errors import AiQuotaExhausted, InvalidCursor, JournalNotFound, UpdateNotFound
from app.service.model import (
    DiagnosisStatus,
    Drainage,
    FingerTest,
    Lighting,
    NewUpdate,
    PotWeight,
)
from app.service.updates import UpdateService
from tests.unit.fakes import FakeAiQuota, FakeUnitOfWork

USER = uuid.uuid4()


def new_update(journal_id: uuid.UUID, update_id: uuid.UUID | None = None) -> NewUpdate:
    return NewUpdate(
        update_id or uuid.uuid4(),
        journal_id,
        None,
        7,
        Drainage.GOOD,
        Lighting.MEDIUM,
        FingerTest.DRY,
        PotWeight.LIGHTER,
        None,
    )


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def quota() -> FakeAiQuota:
    return FakeAiQuota()


@pytest.fixture
def service(uow: FakeUnitOfWork, quota: FakeAiQuota) -> UpdateService:
    return UpdateService(lambda: uow, quota, default_page_size=2, max_page_size=3)


def test_an_update_is_stored_with_a_pending_diagnosis_under_the_reserved_id(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(USER)
    update = new_update(journal.id)

    submitted = service.submit(USER, update)

    (reservation_id,) = quota.reserved
    assert submitted.created
    assert submitted.item.update.id == update.id
    assert submitted.item.diagnosis is not None
    assert submitted.item.diagnosis.status is DiagnosisStatus.PENDING
    assert uow.diagnoses.rows == {update.id: reservation_id}
    assert uow.commits == 1


def test_a_retried_request_returns_the_stored_update_without_spending_quota(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(USER)
    update = new_update(journal.id)
    service.submit(USER, update)

    again = service.submit(USER, update)

    assert not again.created
    assert len(quota.reserved) == 1
    assert len(uow.updates.rows) == 1


def test_an_exhausted_quota_blocks_the_update(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(USER)
    quota.exhausted = True

    with pytest.raises(AiQuotaExhausted):
        service.submit(USER, new_update(journal.id))

    assert uow.updates.rows == {}
    assert uow.commits == 0


def test_an_unavailable_quota_service_still_accepts_the_update_as_pending(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(USER)
    quota.unavailable = True

    submitted = service.submit(USER, new_update(journal.id))

    assert submitted.created
    assert len(uow.diagnoses.rows) == 1
    assert quota.released == []


def test_an_update_to_a_journal_of_another_user_is_not_found_and_spends_nothing(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(uuid.uuid4())

    with pytest.raises(JournalNotFound):
        service.submit(USER, new_update(journal.id))

    assert quota.reserved == []


def test_an_id_that_belongs_to_another_journal_is_not_found(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    first = uow.journals.seed(USER)
    second = uow.journals.seed(USER)
    taken = uow.updates.seed(first.id)

    with pytest.raises(UpdateNotFound):
        service.submit(USER, new_update(second.id, taken.id))

    assert quota.reserved == []


def test_a_failed_commit_releases_the_reservation(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(USER)
    uow.fail_commit_with = RuntimeError("database down")

    with pytest.raises(RuntimeError):
        service.submit(USER, new_update(journal.id))

    assert quota.released == quota.reserved


def test_a_failed_release_after_a_failed_commit_keeps_the_original_error(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(USER)
    uow.fail_commit_with = RuntimeError("database down")
    quota.release_unavailable = True

    with pytest.raises(RuntimeError, match="database down"):
        service.submit(USER, new_update(journal.id))


def test_losing_the_insert_race_releases_the_reservation_and_returns_the_winner(
    service: UpdateService, uow: FakeUnitOfWork, quota: FakeAiQuota, monkeypatch
):
    journal = uow.journals.seed(USER)
    update = new_update(journal.id)

    def another_request_wins(_: NewUpdate) -> bool:
        uow.updates.rows[update.id] = uow.updates.seed(journal.id)
        return False

    monkeypatch.setattr(uow.updates, "add", another_request_wins)

    submitted = service.submit(USER, update)

    assert not submitted.created
    assert quota.released == quota.reserved != []


def test_the_feed_pages_newest_first_and_the_cursor_continues_after_the_last_item(
    service: UpdateService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER)
    seeded = [uow.updates.seed(journal.id) for _ in range(3)]

    first = service.feed(USER, journal.id, None, None)
    assert first.next_cursor is not None
    second = service.feed(USER, journal.id, None, first.next_cursor)

    assert [i.update.id for i in first.items] == [seeded[2].id, seeded[1].id]
    assert [i.update.id for i in second.items] == [seeded[0].id]
    assert second.next_cursor is None


def test_the_feed_asks_for_one_extra_item_and_caps_the_page_size(
    service: UpdateService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER)

    service.feed(USER, journal.id, 50, None)

    assert uow.updates.feed_calls == [(journal.id, 4, None)]


def test_the_feed_of_a_journal_of_another_user_is_not_found(
    service: UpdateService, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(uuid.uuid4())

    with pytest.raises(JournalNotFound):
        service.feed(USER, journal.id, None, None)


def test_a_malformed_cursor_is_rejected(service: UpdateService, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)

    with pytest.raises(InvalidCursor):
        service.feed(USER, journal.id, None, "garbage")
