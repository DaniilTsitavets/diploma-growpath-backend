import uuid

import pytest

from app.service.errors import UpdateNotFound
from app.service.model import PhotoKind
from app.service.photos import PhotoService, PhotoStorageUnavailable, storage_key_for
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import FakeUnitOfWork

USER = uuid.uuid4()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def storage() -> FakePhotoStorage:
    return FakePhotoStorage()


@pytest.fixture
def service(uow: FakeUnitOfWork, storage: FakePhotoStorage) -> PhotoService:
    return PhotoService(lambda: uow, storage, url_ttl_seconds=600)


def seeded_update(uow: FakeUnitOfWork, user_id: uuid.UUID = USER):
    return uow.updates.seed(uow.journals.seed(user_id).id)


def test_an_upload_records_the_key_and_signs_a_put_url(service: PhotoService, uow: FakeUnitOfWork):
    update = seeded_update(uow)
    key = storage_key_for(update.journal_id, update.id, PhotoKind.SOIL)

    upload = service.request_upload(update.id, USER, PhotoKind.SOIL)

    assert upload.url == f"http://storage.test/{key}?method=put"
    assert upload.expires_in_seconds == 600
    assert [(p.kind, p.storage_key) for p in uow.photos.rows] == [(PhotoKind.SOIL, key)]
    assert uow.commits == 1


def test_repeating_an_upload_request_keeps_one_row_and_issues_a_url_again(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow)

    first = service.request_upload(update.id, USER, PhotoKind.OVERVIEW)
    again = service.request_upload(update.id, USER, PhotoKind.OVERVIEW)

    assert first.url == again.url
    assert len(uow.photos.rows) == 1


def test_an_upload_for_an_update_of_another_user_is_not_found(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow, uuid.uuid4())

    with pytest.raises(UpdateNotFound):
        service.request_upload(update.id, USER, PhotoKind.OVERVIEW)

    assert uow.photos.rows == []


def test_a_storage_failure_stores_no_row(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    storage.unavailable = True

    with pytest.raises(PhotoStorageUnavailable):
        service.request_upload(update.id, USER, PhotoKind.OVERVIEW)

    assert uow.photos.rows == []
    assert uow.commits == 0


def test_listing_signs_a_get_url_per_photo_and_reports_the_missing_required_kinds(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow)
    service.request_upload(update.id, USER, PhotoKind.OVERVIEW)
    service.request_upload(update.id, USER, PhotoKind.SYMPTOM_CLOSEUP)

    listed = service.list_photos(update.id, USER)

    assert [(link.kind, link.url.endswith("method=get")) for link in listed.photos] == [
        (PhotoKind.OVERVIEW, True),
        (PhotoKind.SYMPTOM_CLOSEUP, True),
    ]
    assert listed.missing_required == [PhotoKind.SOIL]


def test_nothing_is_missing_once_both_required_photos_exist(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow)
    service.request_upload(update.id, USER, PhotoKind.SOIL)
    service.request_upload(update.id, USER, PhotoKind.OVERVIEW)

    assert service.list_photos(update.id, USER).missing_required == []


def test_listing_photos_of_another_users_update_is_not_found(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow, uuid.uuid4())

    with pytest.raises(UpdateNotFound):
        service.list_photos(update.id, USER)
