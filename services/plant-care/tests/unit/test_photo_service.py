import uuid

import pytest

from app.service.errors import (
    PhotoNotUploaded,
    PhotoTooLarge,
    PhotoTypeNotAllowed,
    RootsPhotoNotRequested,
    UpdateNotFound,
)
from app.service.model import DiagnosisStatus, PhotoKind
from app.service.photos import PhotoService, PhotoStorageUnavailable, storage_key_for
from tests.unit.fake_storage import MAX_BYTES, FakePhotoStorage, photo_policy
from tests.unit.fakes import FakeUnitOfWork

USER = uuid.uuid4()
PNG = "image/png"


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def storage() -> FakePhotoStorage:
    return FakePhotoStorage()


@pytest.fixture
def service(uow: FakeUnitOfWork, storage: FakePhotoStorage) -> PhotoService:
    return PhotoService(lambda: uow, storage, photo_policy())


def seeded_update(uow: FakeUnitOfWork, user_id: uuid.UUID = USER):
    return uow.updates.seed(uow.journals.seed(user_id).id)


def upload_and_confirm(
    service: PhotoService, storage: FakePhotoStorage, update, kind: PhotoKind
) -> None:
    service.request_upload(update.id, USER, kind, PNG, 1000)
    storage.put(storage_key_for(update.journal_id, update.id, kind))
    service.confirm_upload(update.id, USER, kind)


def test_an_upload_signs_a_put_url_and_stores_nothing(service: PhotoService, uow: FakeUnitOfWork):
    update = seeded_update(uow)
    key = storage_key_for(update.journal_id, update.id, PhotoKind.SOIL)

    upload = service.request_upload(update.id, USER, PhotoKind.SOIL, PNG, 1234)

    assert upload.url == f"http://storage.test/{key}?method=put"
    assert upload.expires_in_seconds == 600
    assert (upload.content_type, upload.size_bytes) == (PNG, 1234)
    assert uow.photos.rows == []
    assert uow.commits == 0


@pytest.mark.parametrize("size", [1, MAX_BYTES])
def test_the_size_limits_are_inclusive(service: PhotoService, uow: FakeUnitOfWork, size: int):
    update = seeded_update(uow)

    assert service.request_upload(update.id, USER, PhotoKind.SOIL, PNG, size).size_bytes == size


@pytest.mark.parametrize("size", [0, -1, MAX_BYTES + 1])
def test_a_declared_size_outside_the_limits_is_refused(
    service: PhotoService, uow: FakeUnitOfWork, size: int
):
    update = seeded_update(uow)

    with pytest.raises(PhotoTooLarge):
        service.request_upload(update.id, USER, PhotoKind.SOIL, PNG, size)


@pytest.mark.parametrize("content_type", ["image/gif", "application/pdf", "", "IMAGE/PNG"])
def test_a_declared_type_that_is_not_allowed_is_refused(
    service: PhotoService, uow: FakeUnitOfWork, content_type: str
):
    update = seeded_update(uow)

    with pytest.raises(PhotoTypeNotAllowed):
        service.request_upload(update.id, USER, PhotoKind.SOIL, content_type, 1000)


def test_an_upload_for_an_update_of_another_user_is_not_found(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow, uuid.uuid4())

    with pytest.raises(UpdateNotFound):
        service.request_upload(update.id, USER, PhotoKind.OVERVIEW, PNG, 1000)


def test_a_storage_failure_while_signing_is_reported(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    storage.unavailable = True

    with pytest.raises(PhotoStorageUnavailable):
        service.request_upload(update.id, USER, PhotoKind.OVERVIEW, PNG, 1000)


def test_confirming_records_the_key_and_returns_a_download_link(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    key = storage_key_for(update.journal_id, update.id, PhotoKind.SOIL)
    storage.put(key)

    link = service.confirm_upload(update.id, USER, PhotoKind.SOIL)

    assert (link.kind, link.url) == (PhotoKind.SOIL, f"http://storage.test/{key}?method=get")
    assert [(p.kind, p.storage_key) for p in uow.photos.rows] == [(PhotoKind.SOIL, key)]
    assert uow.commits == 1


def test_confirming_twice_keeps_one_row(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    storage.put(storage_key_for(update.journal_id, update.id, PhotoKind.OVERVIEW))

    first = service.confirm_upload(update.id, USER, PhotoKind.OVERVIEW)
    again = service.confirm_upload(update.id, USER, PhotoKind.OVERVIEW)

    assert first == again
    assert len(uow.photos.rows) == 1


def test_confirming_without_an_uploaded_object_is_a_conflict(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow)

    with pytest.raises(PhotoNotUploaded):
        service.confirm_upload(update.id, USER, PhotoKind.OVERVIEW)

    assert uow.photos.rows == []


@pytest.mark.parametrize("size", [0, MAX_BYTES + 1])
def test_an_object_of_a_bad_size_is_deleted_and_not_recorded(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage, size: int
):
    update = seeded_update(uow)
    key = storage_key_for(update.journal_id, update.id, PhotoKind.SOIL)
    storage.put(key, size_bytes=size)

    with pytest.raises(PhotoTooLarge):
        service.confirm_upload(update.id, USER, PhotoKind.SOIL)

    assert storage.deleted == [key]
    assert key not in storage.objects
    assert uow.photos.rows == []


@pytest.mark.parametrize("content_type", ["image/gif", "text/html", None])
def test_an_object_of_another_type_is_deleted_and_not_recorded(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage, content_type
):
    update = seeded_update(uow)
    key = storage_key_for(update.journal_id, update.id, PhotoKind.SOIL)
    storage.put(key, content_type=content_type)

    with pytest.raises(PhotoTypeNotAllowed):
        service.confirm_upload(update.id, USER, PhotoKind.SOIL)

    assert storage.deleted == [key]
    assert uow.photos.rows == []


def test_a_rejected_replacement_removes_the_confirmed_photo_with_its_object(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    upload_and_confirm(service, storage, update, PhotoKind.SOIL)
    storage.put(storage_key_for(update.journal_id, update.id, PhotoKind.SOIL), MAX_BYTES + 1)

    with pytest.raises(PhotoTooLarge):
        service.confirm_upload(update.id, USER, PhotoKind.SOIL)

    assert uow.photos.rows == []


def test_signing_again_keeps_the_confirmed_photo(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    upload_and_confirm(service, storage, update, PhotoKind.SOIL)

    service.request_upload(update.id, USER, PhotoKind.SOIL, PNG, 2000)

    assert [p.kind for p in uow.photos.rows] == [PhotoKind.SOIL]


def test_confirming_for_an_update_of_another_user_is_not_found(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow, uuid.uuid4())

    with pytest.raises(UpdateNotFound):
        service.confirm_upload(update.id, USER, PhotoKind.OVERVIEW)


def test_a_storage_failure_while_confirming_is_reported_and_stores_nothing(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    storage.unavailable = True

    with pytest.raises(PhotoStorageUnavailable):
        service.confirm_upload(update.id, USER, PhotoKind.OVERVIEW)

    assert uow.photos.rows == []
    assert uow.commits == 0


def test_listing_counts_only_confirmed_photos(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    upload_and_confirm(service, storage, update, PhotoKind.OVERVIEW)
    service.request_upload(update.id, USER, PhotoKind.SOIL, PNG, 1000)

    listed = service.list_photos(update.id, USER)

    assert [(link.kind, link.url.endswith("method=get")) for link in listed.photos] == [
        (PhotoKind.OVERVIEW, True)
    ]
    assert listed.missing_required == [PhotoKind.SOIL]


def test_nothing_is_missing_once_both_required_photos_are_confirmed(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    upload_and_confirm(service, storage, update, PhotoKind.SOIL)
    upload_and_confirm(service, storage, update, PhotoKind.OVERVIEW)

    assert service.list_photos(update.id, USER).missing_required == []


def test_listing_photos_of_another_users_update_is_not_found(
    service: PhotoService, uow: FakeUnitOfWork
):
    update = seeded_update(uow, uuid.uuid4())

    with pytest.raises(UpdateNotFound):
        service.list_photos(update.id, USER)


@pytest.mark.parametrize(
    "diagnosis",
    [
        None,
        {"status": DiagnosisStatus.PENDING},
        {"status": DiagnosisStatus.AWAITING_CLARIFICATION, "clarifying_question": "Q?"},
        {
            "status": DiagnosisStatus.COMPLETED,
            "model_effort": "high",
            "roots_photo_requested": True,
        },
        {
            "status": DiagnosisStatus.DEGRADED,
            "model_effort": "high",
            "clarifying_question": "Q?",
            "roots_photo_requested": True,
        },
    ],
)
def test_a_roots_photo_is_refused_unless_the_ai_is_awaiting_one(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage, diagnosis: dict | None
):
    update = seeded_update(uow)
    storage.put(storage_key_for(update.journal_id, update.id, PhotoKind.ROOTS_DRAINAGE))
    if diagnosis is not None:
        uow.diagnoses.seed(update.id, **diagnosis)

    with pytest.raises(RootsPhotoNotRequested):
        service.request_upload(update.id, USER, PhotoKind.ROOTS_DRAINAGE, PNG, 1000)
    with pytest.raises(RootsPhotoNotRequested):
        service.confirm_upload(update.id, USER, PhotoKind.ROOTS_DRAINAGE)

    assert uow.photos.rows == []
    assert uow.commits == 0


def test_a_roots_photo_is_accepted_while_the_ai_awaits_it(
    service: PhotoService, uow: FakeUnitOfWork, storage: FakePhotoStorage
):
    update = seeded_update(uow)
    uow.diagnoses.seed(
        update.id,
        status=DiagnosisStatus.AWAITING_CLARIFICATION,
        model_effort="high",
        clarifying_question="Q?",
        roots_photo_requested=True,
    )

    upload_and_confirm(service, storage, update, PhotoKind.ROOTS_DRAINAGE)

    assert [p.kind for p in uow.photos.rows] == [PhotoKind.ROOTS_DRAINAGE]


@pytest.mark.parametrize("kind", [PhotoKind.OVERVIEW, PhotoKind.SOIL, PhotoKind.SYMPTOM_CLOSEUP])
def test_the_other_kinds_need_no_request_from_the_ai(
    service: PhotoService, uow: FakeUnitOfWork, kind: PhotoKind
):
    update = seeded_update(uow)
    uow.diagnoses.seed(
        update.id,
        status=DiagnosisStatus.COMPLETED,
        model_effort="high",
        has_dominant_candidate=True,
        recommendation="r",
    )

    assert service.request_upload(update.id, USER, kind, PNG, 1000).kind is kind
