from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CORRELATION_HEADER
from pydantic import BaseModel

from app.main import create_app
from app.service.errors import Conflict, InvalidRequest, NotFound, Unavailable
from tests.unit.fake_queue import FakeQueue
from tests.unit.settings import make_settings

PROBLEM_JSON = "application/problem+json"


class Taken(Conflict):
    code = "taken"


class Connection:
    def execute(self, statement) -> None:
        return None


class HealthyEngine:
    @contextmanager
    def connect(self):
        yield Connection()


def raising(error: Exception):
    def route() -> None:
        raise error

    return route


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def app(queue: FakeQueue) -> FastAPI:
    return create_app(make_settings(), queue)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_live_is_ok(client: TestClient):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_problem_when_database_down(client: TestClient):
    response = client.get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["title"] == "Service Unavailable"
    assert body["detail"] == "database unavailable"


def test_ready_is_ok_when_database_and_queue_answer(app: FastAPI, client: TestClient):
    app.state.engine = HealthyEngine()

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_names_the_queue_when_it_is_unreachable(
    app: FastAPI, client: TestClient, queue: FakeQueue
):
    app.state.engine = HealthyEngine()
    queue.check_fails = True

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "message queue unavailable"


def test_unknown_route_is_problem_json_with_correlation_id(client: TestClient):
    response = client.get("/nope", headers={CORRELATION_HEADER: "corr-9"})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["correlation_id"] == "corr-9"
    assert response.headers[CORRELATION_HEADER] == "corr-9"


def test_invalid_request_is_422_problem_json(app: FastAPI, client: TestClient):
    class Payload(BaseModel):
        quantity: int

    @app.post("/echo")
    def echo(payload: Payload) -> Payload:
        return payload

    response = client.post("/echo", json={"quantity": "many"})

    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["detail"] == "Request does not match the contract"
    assert body["errors"][0]["loc"] == ["body", "quantity"]


def test_unhandled_error_is_500_problem_json_without_details(app: FastAPI, client: TestClient):
    app.add_api_route("/boom", raising(RuntimeError("secret internals")))

    response = client.get("/boom", headers={CORRELATION_HEADER: "corr-1"})

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["detail"] is None
    assert "secret internals" not in response.text
    assert response.headers[CORRELATION_HEADER] == "corr-1"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (NotFound("gone"), 404, None),
        (Taken("taken"), 409, "taken"),
        (InvalidRequest("bad"), 422, None),
        (Unavailable("storage"), 503, None),
    ],
    ids=["not found", "conflict", "invalid request", "unavailable"],
)
def test_application_errors_map_to_problem_responses(
    app: FastAPI, client: TestClient, error: Exception, status: int, code: str | None
):
    app.add_api_route("/fail", raising(error))

    response = client.get("/fail")

    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json().get("code") == code
