import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CORRELATION_HEADER
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.main import create_app
from tests.unit.fakes import make_settings

PROBLEM_JSON = "application/problem+json"


@pytest.fixture
def app() -> FastAPI:
    return create_app(make_settings())


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_live_is_ok(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_problem_when_database_down(client):
    response = client.get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["title"] == "Service Unavailable"
    assert body["detail"] == "database unavailable"


def test_unknown_route_is_problem_json_with_correlation_id(client):
    response = client.get("/nope", headers={CORRELATION_HEADER: "corr-9"})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["correlation_id"] == "corr-9"
    assert response.headers[CORRELATION_HEADER] == "corr-9"


def test_invalid_request_is_422_problem_json(app, client):
    class Payload(BaseModel):
        quantity: int

    @app.post("/echo")
    def echo(payload: Payload) -> Payload:
        return payload

    response = client.post("/echo", json={"quantity": "many"})

    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["errors"][0]["loc"] == ["body", "quantity"]


def test_unexpected_error_is_500_problem_json(app, client):
    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["title"] == "Internal Server Error"
    assert "secret internals" not in response.text


def test_settings_require_database_url(monkeypatch):
    monkeypatch.delenv("ENTITLEMENT_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_settings_do_not_reveal_database_url():
    settings = make_settings(database_url="postgresql://user:hunter2@db/entitlement")

    assert "hunter2" not in repr(settings)


def test_error_log_is_json_with_correlation_id(capsys):
    app = create_app(make_settings())

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/boom", headers={CORRELATION_HEADER: "corr-log"}
    )

    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    (entry,) = [e for e in entries if e["message"] == "unhandled error"]
    assert entry["service"] == "entitlement"
    assert entry["level"] == "ERROR"
    assert entry["correlation_id"] == "corr-log"
    assert response.json()["correlation_id"] == "corr-log"
    assert response.headers[CORRELATION_HEADER] == "corr-log"
    assert "RuntimeError: secret internals" in entry["exception"]
