from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.main import create_app
from tests.unit.fake_queue import FakeQueue
from tests.unit.settings import make_settings


def ready(database_url: URL, queue: FakeQueue):
    settings = make_settings(database_url=database_url.render_as_string(hide_password=False))
    app = create_app(settings, queue)
    return TestClient(app, raise_server_exceptions=False).get("/health/ready")


def test_ready_is_ok_when_database_and_queue_are_reachable(database_url: URL):
    response = ready(database_url, FakeQueue())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_not_ready_when_the_queue_is_unreachable(database_url: URL):
    queue = FakeQueue()
    queue.check_fails = True

    response = ready(database_url, queue)

    assert response.status_code == 503
    assert response.json()["detail"] == "message queue unavailable"
