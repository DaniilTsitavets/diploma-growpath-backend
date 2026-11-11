import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import photos
from app.api.errors import register_error_handlers
from app.service.photos import PhotoService
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import FakeUnitOfWork

PROBLEM_JSON = "application/problem+json"
USER = uuid.uuid4()
AS_USER = {"X-User-Id": str(USER)}


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def storage() -> FakePhotoStorage:
    return FakePhotoStorage()


@pytest.fixture
def client(uow: FakeUnitOfWork, storage: FakePhotoStorage) -> TestClient:
    app = FastAPI()
    app.state.photo_service = PhotoService(lambda: uow, storage, 600)
    register_error_handlers(app)
    app.include_router(photos.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def update_id(uow: FakeUnitOfWork) -> uuid.UUID:
    return uow.updates.seed(uow.journals.seed(USER).id).id


def test_an_upload_url_is_issued_for_a_kind(client: TestClient, update_id: uuid.UUID):
    response = client.post(
        f"/v1/updates/{update_id}/photos", json={"kind": "overview"}, headers=AS_USER
    )

    body = response.json()
    assert response.status_code == 200
    assert body["kind"] == "overview"
    assert body["upload_url"].endswith("/overview?method=put")
    assert body["expires_in_seconds"] == 600


@pytest.mark.parametrize("body", [{}, {"kind": "selfie"}, {"kind": "soil", "size": 1}])
def test_an_unknown_kind_or_field_is_422(client: TestClient, update_id: uuid.UUID, body: dict):
    response = client.post(f"/v1/updates/{update_id}/photos", json=body, headers=AS_USER)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON


def test_the_update_of_another_user_is_404(client: TestClient, uow: FakeUnitOfWork):
    foreign = uow.updates.seed(uow.journals.seed(uuid.uuid4()).id).id

    response = client.post(
        f"/v1/updates/{foreign}/photos", json={"kind": "overview"}, headers=AS_USER
    )

    assert response.status_code == 404


def test_a_storage_outage_is_503_problem_json(
    client: TestClient, update_id: uuid.UUID, storage: FakePhotoStorage
):
    storage.unavailable = True

    response = client.post(
        f"/v1/updates/{update_id}/photos", json={"kind": "overview"}, headers=AS_USER
    )

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON


def test_the_photos_are_listed_with_download_urls_and_the_missing_kinds(
    client: TestClient, update_id: uuid.UUID
):
    client.post(f"/v1/updates/{update_id}/photos", json={"kind": "overview"}, headers=AS_USER)

    response = client.get(f"/v1/updates/{update_id}/photos", headers=AS_USER)

    body = response.json()
    assert response.status_code == 200
    assert [(p["kind"], p["url"].endswith("method=get")) for p in body["photos"]] == [
        ("overview", True)
    ]
    assert body["missing_required"] == ["soil"]
