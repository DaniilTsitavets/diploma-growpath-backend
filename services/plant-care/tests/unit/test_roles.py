import uuid
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import current_user_id, parse_groups
from app.api.errors import register_error_handlers
from app.main import create_app
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.settings import make_settings

PROBLEM_JSON = "application/problem+json"
USER = uuid.uuid4()


@pytest.fixture
def guard_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/guarded")
    def guarded(user_id: Annotated[uuid.UUID, Depends(current_user_id)]) -> dict:
        return {"user_id": str(user_id)}

    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    app = create_app(
        make_settings(), FakeQueue(), photo_storage=FakePhotoStorage(), ticket_queue=FakeQueue()
    )
    return TestClient(app, raise_server_exceptions=False)


def as_user(groups: str | None) -> dict[str, str]:
    headers = {"X-User-Id": str(USER)}
    if groups is not None:
        headers["X-User-Groups"] = groups
    return headers


@pytest.mark.parametrize("groups", ["users", "[users]", " users ", "'users'"])
def test_a_customer_passes_in_any_header_format(guard_client: TestClient, groups: str):
    response = guard_client.get("/guarded", headers=as_user(groups))

    assert response.status_code == 200
    assert response.json() == {"user_id": str(USER)}


@pytest.mark.parametrize(
    "groups",
    ["experts", "admins", "users,experts", "[users, admins]", "users,experts,admins", "", None],
)
def test_anyone_but_a_pure_customer_is_forbidden(guard_client: TestClient, groups: str | None):
    response = guard_client.get("/guarded", headers=as_user(groups))

    body = response.json()
    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["code"] == "forbidden"
    assert body["detail"] == "this role cannot use this endpoint"


def test_a_missing_user_id_is_unauthorized_even_with_groups(guard_client: TestClient):
    response = guard_client.get("/guarded", headers={"X-User-Groups": "users"})

    assert response.status_code == 401


def test_a_malformed_user_id_is_unauthorized(guard_client: TestClient):
    headers = {"X-User-Id": "me", "X-User-Groups": "users"}

    assert guard_client.get("/guarded", headers=headers).status_code == 401


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, set()),
        ("users", {"users"}),
        ("users,experts", {"users", "experts"}),
        ("[users, admins]", {"users", "admins"}),
    ],
)
def test_groups_are_parsed_from_the_gateway_rendering(header: str | None, expected: set[str]):
    assert parse_groups(header) == expected


@pytest.mark.parametrize("groups", ["experts", "admins", "users,experts"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/v1/journals/{uuid.uuid4()}"),
        ("POST", f"/v1/journals/{uuid.uuid4()}/updates"),
        ("POST", f"/v1/updates/{uuid.uuid4()}/photos"),
        ("POST", f"/v1/updates/{uuid.uuid4()}/diagnosis"),
        ("POST", f"/v1/updates/{uuid.uuid4()}/escalation"),
        ("POST", f"/v1/journals/{uuid.uuid4()}/chat/turns"),
    ],
)
def test_every_customer_router_refuses_staff(
    client: TestClient, method: str, path: str, groups: str
):
    response = client.request(method, path, headers=as_user(groups), json={})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_the_plant_page_stays_open_without_identity(client: TestClient):
    response = client.get("/v1/plants/lavender")

    assert response.status_code != 401 and response.status_code != 403
