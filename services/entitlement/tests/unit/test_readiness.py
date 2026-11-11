from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.db import get_engine
from app.main import create_app
from tests.unit.fakes import FakeQueue, make_settings


class ReachableEngine:
    @contextmanager
    def connect(self):
        yield type("Connection", (), {"execute": lambda self, statement: None})()


def ready(orders: FakeQueue, tickets: FakeQueue):
    app = create_app(make_settings(), orders, tickets)
    app.dependency_overrides[get_engine] = ReachableEngine
    return TestClient(app, raise_server_exceptions=False).get("/health/ready")


def test_ready_when_the_database_and_both_queues_answer():
    response = ready(FakeQueue(), FakeQueue())

    assert (response.status_code, response.json()) == (200, {"status": "ok"})


@pytest.mark.parametrize("broken", ["orders", "tickets"])
def test_not_ready_when_a_queue_is_unreachable(broken: str):
    queues = {"orders": FakeQueue(), "tickets": FakeQueue()}
    queues[broken].check_fails = True

    response = ready(**queues)

    assert response.status_code == 503
    assert response.json()["detail"] == "message queue unavailable"
