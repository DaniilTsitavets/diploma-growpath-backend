import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from app.main import create_app
from app.service.directory import Role
from app.service.expert_tickets import ExpertTicketService
from app.service.ticket_publisher import TerminalEventPublisher
from app.service.tickets import TicketService
from tests.unit.contracts import (
    assert_conforms,
    build_openapi,
    load_openapi,
    openapi_path,
    operations_of,
    render_yaml,
    routes_of,
)
from tests.unit.fake_directory import FakeUserDirectory, make_user
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import TTL_SECONDS, FakeUnitOfWork, RecordingEvents, make_ticket
from tests.unit.settings import make_settings

SERVICE = "expert-consultation"
DESCRIPTION = (
    "Expert tickets: a state machine open, then answered or cancelled (cancelled only by expiry), "
    "the context snapshot and the expert answer. Consumes `escalation.requested`, publishes "
    "`ticket.created`, `ticket.answered` and `ticket.cancelled`. Identity arrives in the "
    "`X-User-Id` and `X-User-Groups` headers, which the API gateway sets from the verified JWT; "
    "every `/v1/expert/**` operation requires the `experts` group. The service also hosts the "
    "identity administration: `/v1/admin/users/**` requires the `admins` group and manages "
    "Cognito users and their role (the Cognito group `users`, `experts` or `admins`; a user has "
    "exactly one), and `/v1/me` answers any authenticated caller from the identity headers. "
    "The `internal` tag marks operations the gateway does not expose."
)
PUBLIC_PREFIXES = ("/v1/expert/tickets", "/v1/admin/users", "/v1/me")
EXPERT = uuid.uuid4()
ADMIN = uuid.uuid4()
AS_EXPERT = {"X-User-Id": str(EXPERT), "X-User-Groups": "experts"}
AS_ADMIN = {"X-User-Id": str(ADMIN), "X-User-Groups": "admins"}
PASSWORD = "S3cret-pass-phrase"


def build_app(directory: FakeUserDirectory | None = None) -> FastAPI:
    return create_app(
        make_settings(),
        FakeQueue(),
        RecordingEvents(),
        FakePhotoStorage(),
        directory or FakeUserDirectory(),
    )


def test_the_committed_openapi_is_what_the_app_generates():
    expected = render_yaml(build_openapi(build_app(), DESCRIPTION, PUBLIC_PREFIXES))
    path = openapi_path(SERVICE)
    if os.environ.get("UPDATE_CONTRACTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        return

    assert path.read_text(encoding="utf-8") == expected, (
        f"{path.name} is out of date with the app: run `make contracts`"
    )


def test_every_route_is_in_the_contract_and_the_other_way_round():
    documented = operations_of(load_openapi(SERVICE))

    assert routes_of(build_app()) == documented


def test_the_openapi_document_is_valid():
    validator = pytest.importorskip("openapi_spec_validator")

    validator.validate(load_openapi(SERVICE))


def test_the_ticket_and_identity_operations_are_public_and_health_is_internal():
    document = load_openapi(SERVICE)

    exposure = {
        path: operation["x-gateway"]
        for path, item in document["paths"].items()
        for operation in item.values()
    }
    assert {path for path, value in exposure.items() if value == "public"} == {
        "/v1/expert/tickets",
        "/v1/expert/tickets/{ticket_id}",
        "/v1/expert/tickets/{ticket_id}/answer",
        "/v1/admin/users",
        "/v1/admin/users/{user_id}/role",
        "/v1/admin/users/{user_id}/disable",
        "/v1/admin/users/{user_id}/enable",
        "/v1/admin/users/{user_id}/password",
        "/v1/me",
    }
    assert "internal" in document["paths"]["/health/ready"]["get"]["tags"]


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def client(uow: FakeUnitOfWork) -> TestClient:
    app = build_app()
    events = RecordingEvents(uow.log)
    app.state.ticket_service = TicketService(
        lambda: uow, events, TerminalEventPublisher(lambda: uow, events), TTL_SECONDS
    )
    app.state.expert_ticket_service = ExpertTicketService(lambda: uow, FakePhotoStorage(), 2, 3)
    return TestClient(app, raise_server_exceptions=False)


def test_the_ticket_queue_conforms(client: TestClient, uow: FakeUnitOfWork):
    uow.tickets.add(make_ticket())

    response = client.get("/v1/expert/tickets", headers=AS_EXPERT)

    assert response.status_code == 200 and response.json()["items"]
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)


def test_a_ticket_with_its_context_conforms(client: TestClient, uow: FakeUnitOfWork):
    ticket = uow.tickets.add(make_ticket())

    response = client.get(f"/v1/expert/tickets/{ticket.id}", headers=AS_EXPERT)

    assert response.status_code == 200
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets/{ticket_id}", "GET", response)


def test_an_answer_conforms(client: TestClient, uow: FakeUnitOfWork):
    ticket = uow.tickets.add(make_ticket())

    response = client.post(
        f"/v1/expert/tickets/{ticket.id}/answer", json={"body": "Water less."}, headers=AS_EXPERT
    )

    assert response.status_code == 201
    assert_conforms(
        load_openapi(SERVICE), "/v1/expert/tickets/{ticket_id}/answer", "POST", response
    )


def test_a_caller_outside_the_experts_group_conforms_to_the_problem_schema(client: TestClient):
    headers = {"X-User-Id": str(EXPERT), "X-User-Groups": "users"}

    response = client.get("/v1/expert/tickets", headers=headers)

    assert response.status_code == 403
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)


def test_a_missing_user_conforms_to_the_problem_schema(client: TestClient):
    response = client.get("/v1/expert/tickets")

    assert response.status_code == 401
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)


def test_an_invalid_cursor_conforms_to_the_problem_schema(client: TestClient):
    response = client.get("/v1/expert/tickets", params={"cursor": "garbage"}, headers=AS_EXPERT)

    assert response.status_code == 422 and response.json()["code"] == "invalid-cursor"
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)


def test_a_missing_ticket_conforms_to_the_problem_schema(client: TestClient):
    response = client.get(f"/v1/expert/tickets/{uuid.uuid4()}", headers=AS_EXPERT)

    assert response.status_code == 404
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets/{ticket_id}", "GET", response)


def test_an_answer_conflict_conforms_to_the_problem_schema(client: TestClient, uow: FakeUnitOfWork):
    ticket = uow.tickets.add(make_ticket())
    path = f"/v1/expert/tickets/{ticket.id}/answer"
    client.post(path, json={"body": "Water less."}, headers=AS_EXPERT)

    response = client.post(
        path, json={"body": "Water less."}, headers={**AS_EXPERT, "X-User-Id": str(uuid.uuid4())}
    )

    assert response.status_code == 409
    assert_conforms(
        load_openapi(SERVICE), "/v1/expert/tickets/{ticket_id}/answer", "POST", response
    )


def test_every_error_response_of_a_route_is_declared_as_a_problem():
    document = load_openapi(SERVICE)

    declared = {
        (path, method): sorted(code for code in operation["responses"] if code.isdigit())
        for path, item in document["paths"].items()
        for method, operation in item.items()
    }

    assert declared[("/v1/expert/tickets", "get")] == ["200", "401", "403", "422"]
    assert declared[("/v1/expert/tickets/{ticket_id}", "get")] == [
        "200",
        "401",
        "403",
        "404",
        "422",
        "503",
    ]
    assert declared[("/v1/expert/tickets/{ticket_id}/answer", "post")] == [
        "200",
        "201",
        "401",
        "403",
        "404",
        "409",
        "422",
    ]
    assert declared[("/v1/admin/users", "get")] == ["200", "401", "403", "422", "503"]
    assert declared[("/v1/admin/users", "post")] == ["201", "401", "403", "409", "422", "503"]
    assert declared[("/v1/admin/users/{user_id}/role", "put")] == [
        "200",
        "401",
        "403",
        "404",
        "409",
        "422",
        "503",
    ]
    for action in ("disable", "enable"):
        assert declared[(f"/v1/admin/users/{{user_id}}/{action}", "post")] == [
            "204",
            "401",
            "403",
            "404",
            "409",
            "422",
            "503",
        ]
    assert declared[("/v1/admin/users/{user_id}/password", "post")] == [
        "204",
        "401",
        "403",
        "404",
        "422",
        "503",
    ]
    assert declared[("/v1/me", "get")] == ["200", "401", "422"]
    assert declared[("/health/ready", "get")] == ["200", "503"]
    assert "code" in document["components"]["schemas"]["Problem"]["required"]


def test_the_conformance_check_rejects_a_body_that_breaks_the_schema():
    response = Response(200, json={"items": "none"})

    with pytest.raises(AssertionError):
        assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)


@pytest.fixture
def directory() -> FakeUserDirectory:
    directory = FakeUserDirectory()
    directory.add(make_user(Role.ADMIN, user_id=ADMIN))
    return directory


@pytest.fixture
def admin_client(directory: FakeUserDirectory) -> TestClient:
    return TestClient(build_app(directory), raise_server_exceptions=False)


def test_the_user_list_conforms(admin_client: TestClient):
    response = admin_client.get("/v1/admin/users", headers=AS_ADMIN)

    assert response.status_code == 200 and response.json()["items"][0]["role"] == "admin"
    assert_conforms(load_openapi(SERVICE), "/v1/admin/users", "GET", response)


def test_a_created_user_conforms(admin_client: TestClient):
    response = admin_client.post(
        "/v1/admin/users",
        json={"email": "new@example.com", "password": PASSWORD, "role": "user"},
        headers=AS_ADMIN,
    )

    assert response.status_code == 201
    assert_conforms(load_openapi(SERVICE), "/v1/admin/users", "POST", response)


def test_a_changed_role_conforms(admin_client: TestClient, directory: FakeUserDirectory):
    user = directory.add(make_user(Role.USER))

    response = admin_client.put(
        f"/v1/admin/users/{user.user_id}/role", json={"role": "expert"}, headers=AS_ADMIN
    )

    assert response.status_code == 200
    assert_conforms(load_openapi(SERVICE), "/v1/admin/users/{user_id}/role", "PUT", response)


@pytest.mark.parametrize(
    ("method", "path", "body", "status", "code"),
    [
        (
            "POST",
            "/v1/admin/users",
            {"email": "a@b.co", "password": "x", "role": "user"},
            422,
            None,
        ),
        ("PUT", "/v1/admin/users/{admin}/role", {"role": "user"}, 409, "cannot-modify-self"),
        ("POST", "/v1/admin/users/{admin}/disable", None, 409, "cannot-modify-self"),
        ("POST", "/v1/admin/users/{other}/enable", None, 404, "user-not-found"),
        ("POST", "/v1/admin/users/{other}/password", {"password": "x"}, 404, "user-not-found"),
    ],
    ids=["weak-password", "self-role", "self-disable", "unknown-enable", "unknown-password"],
)
def test_admin_errors_conform_to_the_problem_schema(
    admin_client: TestClient, method: str, path: str, body, status: int, code: str | None
):
    template = path.replace("{admin}", str(ADMIN)).replace("{other}", str(uuid.uuid4()))
    response = admin_client.request(method, template, json=body, headers=AS_ADMIN)

    assert response.status_code == status
    assert code is None or response.json()["code"] == code
    contract_path = path.replace("{admin}", "{user_id}").replace("{other}", "{user_id}")
    assert_conforms(load_openapi(SERVICE), contract_path, method, response)


def test_an_unavailable_directory_conforms_to_the_problem_schema(
    admin_client: TestClient, directory: FakeUserDirectory
):
    directory.unavailable = True

    response = admin_client.get("/v1/admin/users", headers=AS_ADMIN)

    assert response.status_code == 503
    assert_conforms(load_openapi(SERVICE), "/v1/admin/users", "GET", response)


def test_a_caller_outside_the_admins_group_conforms_to_the_problem_schema(
    admin_client: TestClient,
):
    response = admin_client.get("/v1/admin/users", headers=AS_EXPERT)

    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    assert_conforms(load_openapi(SERVICE), "/v1/admin/users", "GET", response)


def test_me_conforms(admin_client: TestClient):
    response = admin_client.get("/v1/me", headers=AS_ADMIN)

    assert response.status_code == 200 and response.json()["role"] == "admin"
    assert_conforms(load_openapi(SERVICE), "/v1/me", "GET", response)
    unauthorized = admin_client.get("/v1/me")
    assert unauthorized.status_code == 401
    assert_conforms(load_openapi(SERVICE), "/v1/me", "GET", unauthorized)
