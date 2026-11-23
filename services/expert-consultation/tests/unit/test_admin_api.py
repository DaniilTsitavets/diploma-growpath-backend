import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CorrelationIdMiddleware

from app.api import admin, me
from app.api.errors import register_error_handlers
from app.service.directory import Role
from app.service.users import UserAdminService
from tests.unit.fake_directory import FakeUserDirectory, make_user

PROBLEM_JSON = "application/problem+json"
ADMIN = uuid.uuid4()
AS_ADMIN = {"X-User-Id": str(ADMIN), "X-User-Groups": "admins"}
PASSWORD = "S3cret-pass-phrase"


@pytest.fixture
def directory() -> FakeUserDirectory:
    directory = FakeUserDirectory()
    directory.add(make_user(Role.ADMIN, user_id=ADMIN))
    return directory


@pytest.fixture
def client(directory: FakeUserDirectory) -> TestClient:
    app = FastAPI()
    app.state.user_admin_service = UserAdminService(directory, 2, 3)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app, retry_after_seconds=5)
    app.include_router(admin.router)
    app.include_router(me.router)
    return TestClient(app, raise_server_exceptions=False)


def user_path(user_id: uuid.UUID, action: str) -> str:
    return f"/v1/admin/users/{user_id}/{action}"


ROUTES = [
    ("GET", "/v1/admin/users", None),
    ("POST", "/v1/admin/users", {"email": "a@example.com", "password": PASSWORD, "role": "user"}),
    ("PUT", user_path(ADMIN, "role"), {"role": "user"}),
    ("POST", user_path(ADMIN, "disable"), None),
    ("POST", user_path(ADMIN, "enable"), None),
    ("POST", user_path(ADMIN, "password"), {"password": PASSWORD}),
]
ROUTE_IDS = ["list", "create", "role", "disable", "enable", "password"]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=ROUTE_IDS)
def test_the_user_header_is_required(client: TestClient, method: str, path: str, body):
    response = client.request(method, path, json=body, headers={"X-User-Groups": "admins"})

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=ROUTE_IDS)
@pytest.mark.parametrize("groups", ["experts", "users", "", "[users, experts]"])
def test_only_admins_may_call(client: TestClient, method: str, path: str, body, groups: str):
    headers = {"X-User-Id": str(uuid.uuid4()), "X-User-Groups": groups}

    response = client.request(method, path, json=body, headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_a_malformed_user_header_is_unauthorized(client: TestClient):
    response = client.get("/v1/admin/users", headers={"X-User-Id": "me", "X-User-Groups": "admins"})

    assert response.status_code == 401


def test_users_are_listed_with_role_and_cursor(client: TestClient, directory: FakeUserDirectory):
    directory.add(make_user(Role.EXPERT, email="e@example.com"))
    directory.add(make_user(None))

    first = client.get("/v1/admin/users", headers=AS_ADMIN).json()
    second = client.get(
        "/v1/admin/users", params={"cursor": first["next_cursor"]}, headers=AS_ADMIN
    ).json()

    assert [item["role"] for item in first["items"]] == ["admin", "expert"]
    assert first["items"][0].keys() == {
        "user_id",
        "username",
        "email",
        "role",
        "enabled",
        "created_at",
    }
    assert [item["role"] for item in second["items"]] == [None]
    assert second["next_cursor"] is None


def test_the_page_size_comes_from_the_limit_parameter(client: TestClient):
    response = client.get("/v1/admin/users", params={"limit": 1}, headers=AS_ADMIN)

    assert len(response.json()["items"]) == 1


def test_a_foreign_cursor_is_invalid(client: TestClient):
    response = client.get("/v1/admin/users", params={"cursor": "%%%"}, headers=AS_ADMIN)

    assert response.status_code == 422 and response.json()["code"] == "invalid-cursor"


def test_a_user_is_created(client: TestClient, directory: FakeUserDirectory):
    response = client.post(
        "/v1/admin/users",
        json={"email": "new@example.com", "password": PASSWORD, "role": "expert"},
        headers=AS_ADMIN,
    )

    body = response.json()
    assert response.status_code == 201
    assert body["email"] == "new@example.com" and body["role"] == "expert"
    assert body["enabled"] is True
    assert PASSWORD not in response.text
    assert directory.passwords[uuid.UUID(body["user_id"])] == PASSWORD


def test_creating_an_existing_user_conflicts(client: TestClient):
    payload = {"email": "new@example.com", "password": PASSWORD, "role": "user"}
    client.post("/v1/admin/users", json=payload, headers=AS_ADMIN)

    response = client.post("/v1/admin/users", json=payload, headers=AS_ADMIN)

    assert response.status_code == 409 and response.json()["code"] == "user-exists"


def test_a_weak_password_is_unprocessable_and_not_echoed(client: TestClient):
    response = client.post(
        "/v1/admin/users",
        json={"email": "new@example.com", "password": "weak", "role": "user"},
        headers=AS_ADMIN,
    )

    assert response.status_code == 422 and response.json()["code"] == "invalid-password"
    assert "weak" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD, "role": "user"},
        {"email": "a@example.com", "password": PASSWORD, "role": "owner"},
        {"email": "a@example.com", "role": "user"},
        {"email": "a@example.com", "password": PASSWORD, "role": "user", "extra": 1},
    ],
)
def test_an_invalid_creation_body_is_rejected_without_echoing_input(
    client: TestClient, payload: dict
):
    response = client.post("/v1/admin/users", json=payload, headers=AS_ADMIN)

    assert response.status_code == 422 and response.json()["code"] == "validation-error"
    assert PASSWORD not in response.text


def test_the_role_is_changed(client: TestClient, directory: FakeUserDirectory):
    user = directory.add(make_user(Role.USER))

    response = client.put(
        user_path(user.user_id, "role"), json={"role": "expert"}, headers=AS_ADMIN
    )

    assert response.status_code == 200
    assert response.json()["role"] == "expert" and response.json()["user_id"] == str(user.user_id)


def test_an_admin_cannot_change_their_own_role(client: TestClient):
    response = client.put(user_path(ADMIN, "role"), json={"role": "user"}, headers=AS_ADMIN)

    assert response.status_code == 409 and response.json()["code"] == "cannot-modify-self"


def test_changing_the_role_of_an_unknown_user_is_not_found(client: TestClient):
    response = client.put(user_path(uuid.uuid4(), "role"), json={"role": "user"}, headers=AS_ADMIN)

    assert response.status_code == 404 and response.json()["code"] == "user-not-found"


def test_a_user_is_disabled_and_enabled(client: TestClient, directory: FakeUserDirectory):
    user = directory.add(make_user())

    disabled = client.post(user_path(user.user_id, "disable"), headers=AS_ADMIN)
    assert disabled.status_code == 204 and directory.users[user.user_id].enabled is False

    enabled = client.post(user_path(user.user_id, "enable"), headers=AS_ADMIN)
    assert enabled.status_code == 204 and directory.users[user.user_id].enabled is True


@pytest.mark.parametrize("action", ["disable", "enable"])
def test_an_admin_cannot_disable_or_enable_themselves(client: TestClient, action: str):
    response = client.post(user_path(ADMIN, action), headers=AS_ADMIN)

    assert response.status_code == 409 and response.json()["code"] == "cannot-modify-self"


@pytest.mark.parametrize("action", ["disable", "enable", "password"])
def test_an_unknown_user_is_not_found(client: TestClient, action: str):
    response = client.post(
        user_path(uuid.uuid4(), action), json={"password": PASSWORD}, headers=AS_ADMIN
    )

    assert response.status_code == 404 and response.json()["code"] == "user-not-found"


def test_a_password_is_reset(client: TestClient, directory: FakeUserDirectory):
    user = directory.add(make_user())

    response = client.post(
        user_path(user.user_id, "password"), json={"password": PASSWORD}, headers=AS_ADMIN
    )

    assert response.status_code == 204 and response.content == b""
    assert directory.passwords[user.user_id] == PASSWORD


def test_a_weak_reset_password_is_unprocessable(client: TestClient, directory: FakeUserDirectory):
    user = directory.add(make_user())

    response = client.post(
        user_path(user.user_id, "password"), json={"password": "weak"}, headers=AS_ADMIN
    )

    assert response.status_code == 422 and response.json()["code"] == "invalid-password"


def test_an_unavailable_directory_is_a_503_with_retry_after(
    client: TestClient, directory: FakeUserDirectory
):
    directory.unavailable = True

    response = client.get("/v1/admin/users", headers=AS_ADMIN)

    assert response.status_code == 503
    assert response.json()["code"] == "service-unavailable"
    assert response.headers["retry-after"] == "5"


def test_a_malformed_user_id_in_the_path_is_unprocessable(client: TestClient):
    response = client.post("/v1/admin/users/not-a-uuid/disable", headers=AS_ADMIN)

    assert response.status_code == 422


def test_me_answers_from_the_headers(client: TestClient):
    user_id = uuid.uuid4()

    response = client.get(
        "/v1/me", headers={"X-User-Id": str(user_id), "X-User-Groups": "[users, beta]"}
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id), "role": "user", "groups": ["beta", "users"]}


def test_me_without_a_role_group_has_a_null_role(client: TestClient):
    response = client.get("/v1/me", headers={"X-User-Id": str(uuid.uuid4())})

    assert response.json()["role"] is None and response.json()["groups"] == []


def test_me_requires_the_user_header(client: TestClient):
    response = client.get("/v1/me", headers={"X-User-Groups": "users"})

    assert response.status_code == 401
