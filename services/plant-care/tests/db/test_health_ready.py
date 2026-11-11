from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.main import create_app
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.settings import make_settings


def ready(database_url: URL, order_queue: FakeQueue, ticket_queue: FakeQueue | None = None):
    settings = make_settings(database_url=database_url.render_as_string(hide_password=False))
    app = create_app(
        settings,
        order_queue,
        photo_storage=FakePhotoStorage(),
        ticket_queue=ticket_queue or FakeQueue(),
    )
    return TestClient(app, raise_server_exceptions=False).get("/health/ready")


def test_ready_is_ok_when_database_and_queues_reachable(database_url: URL):
    response = ready(database_url, FakeQueue())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_not_ready_when_the_order_queue_is_unreachable(database_url: URL):
    queue = FakeQueue()
    queue.check_fails = True

    response = ready(database_url, queue)

    assert response.status_code == 503
    assert response.json()["detail"] == "message queue unavailable"


def test_not_ready_when_the_ticket_queue_is_unreachable(database_url: URL):
    queue = FakeQueue()
    queue.check_fails = True

    response = ready(database_url, FakeQueue(), queue)

    assert response.status_code == 503
    assert response.json()["detail"] == "message queue unavailable"
