from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.main import create_app
from tests.unit.fakes import FakeQueue, make_settings


def ready(database_url: URL, tickets: FakeQueue):
    settings = make_settings(database_url=database_url.render_as_string(hide_password=False))
    app = create_app(settings, FakeQueue(), tickets)
    return TestClient(app, raise_server_exceptions=False).get("/health/ready")


def test_ready_is_ok_when_database_and_queues_reachable(database_url: URL):
    response = ready(database_url, FakeQueue())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_not_ready_when_a_queue_is_unreachable(database_url: URL):
    tickets = FakeQueue()
    tickets.check_fails = True

    response = ready(database_url, tickets)

    assert response.status_code == 503
    assert response.json()["detail"] == "message queue unavailable"
