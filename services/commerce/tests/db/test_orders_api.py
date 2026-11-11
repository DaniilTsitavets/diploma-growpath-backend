import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.config import Settings
from app.main import create_app
from app.models import CatalogItem


@pytest.fixture
def client(database_url: URL) -> TestClient:
    settings = Settings(database_url=database_url.render_as_string(hide_password=False))
    return TestClient(create_app(settings), raise_server_exceptions=False)


def post_order(client: TestClient, item_id: uuid.UUID, user_id: uuid.UUID, key: str):
    return client.post(
        "/v1/orders",
        json={"item_id": str(item_id)},
        headers={"Idempotency-Key": key, "X-User-Id": str(user_id)},
    )


def test_order_flow_against_the_database(client: TestClient, discounted_item: CatalogItem):
    user_id = uuid.uuid4()

    first = post_order(client, discounted_item.id, user_id, "key-1")
    repeated = post_order(client, discounted_item.id, user_id, "key-1")
    second = post_order(client, discounted_item.id, user_id, "key-2")

    assert (first.status_code, first.json()["total_paid_cents"]) == (201, 2000)
    assert (repeated.status_code, repeated.json()) == (200, first.json())
    assert (second.status_code, second.json()["discount_cents"]) == (201, 0)


def test_unknown_item_is_404_from_the_database(client: TestClient):
    response = post_order(client, uuid.uuid4(), uuid.uuid4(), "key-1")

    assert response.status_code == 404


def test_catalog_endpoint_reads_the_database(client: TestClient, committed_item: CatalogItem):
    names = [item["name"] for item in client.get("/v1/items").json()]

    assert committed_item.name in names
