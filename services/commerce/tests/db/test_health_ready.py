from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.main import create_app
from tests.unit.fakes import make_settings


def test_ready_is_ok_when_database_reachable(database_url: URL):
    app = create_app(make_settings(database_url=database_url.render_as_string(hide_password=False)))

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
