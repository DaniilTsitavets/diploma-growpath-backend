import pytest
from fastapi.testclient import TestClient
from growpath_observability import CORRELATION_HEADER
from sqlalchemy import create_engine

from app.db import get_engine
from app.main import create_app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("COMMERCE_DATABASE_URL", "sqlite://")
    return create_app()


def test_live_is_ok(app):
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_ok_when_database_reachable(app):
    app.dependency_overrides[get_engine] = lambda: create_engine("sqlite://")

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200


def test_ready_reports_problem_when_database_down(app):
    app.dependency_overrides[get_engine] = lambda: create_engine("sqlite:////nonexistent/dir/db")

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "database unavailable"


def test_unknown_route_is_problem_json_with_correlation_id(app):
    response = TestClient(app).get("/nope", headers={CORRELATION_HEADER: "corr-9"})

    assert response.status_code == 404
    assert response.json()["correlation_id"] == "corr-9"
    assert response.headers[CORRELATION_HEADER] == "corr-9"


def test_unexpected_error_is_500_problem_json(app):
    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert response.json()["title"] == "Internal server error"
    assert "secret internals" not in response.text
