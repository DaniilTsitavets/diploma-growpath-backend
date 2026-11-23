import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import photos
from app.api.errors import register_error_handlers
from app.service.model import PhotoKind
from app.service.photos import PhotoService, storage_key_for
from tests.unit.fake_storage import MAX_BYTES, FakePhotoStorage, photo_policy
from tests.unit.fakes import FakeUnitOfWork

PROBLEM_JSON = "application/problem+json"
USER = uuid.uuid4()
AS_USER = {"X-User-Id": str(USER), "X-User-Groups": "users"}
PNG = {"content_type": "image/png", "size_bytes": 1000}


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def storage() -> FakePhotoStorage:
    return FakePhotoStorage()


@pytest.fixture
def client(uow: FakeUnitOfWork, storage: FakePhotoStorage) -> TestClient:
    app = FastAPI()
    app.state.photo_service = PhotoService(lambda: uow, storage, photo_policy())
    register_error_handlers(app)
    app.include_router(photos.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def update(uow: FakeUnitOfWork):
    return uow.updates.seed(uow.journals.seed(USER).id)


@pytest.fixture
def update_id(update) -> uuid.UUID:
    return update.id


def upload(client: TestClient, update_id: uuid.UUID, kind: str = "overview", **body):
    return client.post(
        f"/v1/updates/{update_id}/photos", json={**PNG, "kind": kind, **body}, headers=AS_USER
    )


def confirm(client: TestClient, update_id: uuid.UUID, kind: str = "overview"):
    return client.post(f"/v1/updates/{update_id}/photos/{kind}/confirm", headers=AS_USER)


def test_an_upload_url_is_issued_with_the_headers_to_send(client: TestClient, update_id):
    response = upload(client, update_id, size_bytes=1234)

    body = response.json()
    assert response.status_code == 200
    assert body["kind"] == "overview"
    assert body["upload_url"].endswith("/overview?method=put")
    assert body["expires_in_seconds"] == 600
    assert body["required_headers"] == {"Content-Type": "image/png", "Content-Length": "1234"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"kind": "selfie", **PNG},
        {"kind": "soil", "content_type": "image/png"},
        {"kind": "soil", "size_bytes": 10},
        {"kind": "soil", "content_type": "image/png", "size_bytes": 0},
        {"kind": "soil", **PNG, "extra": 1},
    ],
)
def test_a_malformed_body_is_422(client: TestClient, update_id: uuid.UUID, body: dict):
    response = client.post(f"/v1/updates/{update_id}/photos", json=body, headers=AS_USER)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"size_bytes": MAX_BYTES + 1}, "photo-too-large"),
        ({"content_type": "image/gif"}, "photo-type-not-allowed"),
    ],
)
def test_a_size_or_type_that_is_not_allowed_is_422_with_a_code(
    client: TestClient, update_id: uuid.UUID, body: dict, code: str
):
    response = upload(client, update_id, **body)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == code


def test_the_update_of_another_user_is_404(client: TestClient, uow: FakeUnitOfWork):
    foreign = uow.updates.seed(uow.journals.seed(uuid.uuid4()).id).id

    assert upload(client, foreign).status_code == 404
    assert confirm(client, foreign).status_code == 404


def test_a_storage_outage_is_503_problem_json(
    client: TestClient, update_id: uuid.UUID, storage: FakePhotoStorage
):
    storage.unavailable = True

    responses = [upload(client, update_id), confirm(client, update_id)]

    assert [r.status_code for r in responses] == [503, 503]
    assert {r.headers["content-type"] for r in responses} == {PROBLEM_JSON}


def test_confirming_an_uploaded_object_returns_the_listed_shape(
    client: TestClient, update, storage: FakePhotoStorage
):
    storage.put(storage_key_for(update.journal_id, update.id, PhotoKind.OVERVIEW))

    first = confirm(client, update.id)
    again = confirm(client, update.id)

    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert first.json()["kind"] == "overview"
    assert first.json()["url"].endswith("method=get")


def test_confirming_without_an_object_is_409_photo_not_uploaded(client: TestClient, update_id):
    response = confirm(client, update_id)

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "photo-not-uploaded"


@pytest.mark.parametrize(
    ("stored", "code"),
    [
        ({"size_bytes": MAX_BYTES + 1}, "photo-too-large"),
        ({"size_bytes": 0}, "photo-too-large"),
        ({"content_type": "image/gif"}, "photo-type-not-allowed"),
    ],
)
def test_confirming_a_bad_object_is_422_and_deletes_it(
    client: TestClient, update, storage: FakePhotoStorage, stored: dict, code: str
):
    key = storage_key_for(update.journal_id, update.id, PhotoKind.OVERVIEW)
    storage.put(key, **stored)

    response = confirm(client, update.id)

    assert response.status_code == 422
    assert response.json()["code"] == code
    assert storage.deleted == [key]


def test_an_unknown_kind_in_the_confirm_path_is_422(client: TestClient, update_id):
    assert confirm(client, update_id, "selfie").status_code == 422


def test_the_photos_are_listed_only_once_confirmed(
    client: TestClient, update, storage: FakePhotoStorage
):
    upload(client, update.id, "overview")
    upload(client, update.id, "soil")
    storage.put(storage_key_for(update.journal_id, update.id, PhotoKind.OVERVIEW))
    confirm(client, update.id, "overview")

    body = client.get(f"/v1/updates/{update.id}/photos", headers=AS_USER).json()

    assert [(p["kind"], p["url"].endswith("method=get")) for p in body["photos"]] == [
        ("overview", True)
    ]
    assert body["missing_required"] == ["soil"]
