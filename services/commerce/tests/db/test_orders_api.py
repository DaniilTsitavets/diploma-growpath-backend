import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import CatalogItem
from tests.db.factories import make_item
from tests.unit.fakes import RecordingEvents, make_settings


@pytest.fixture
def client(database_url: URL) -> TestClient:
    settings = make_settings(database_url=database_url.render_as_string(hide_password=False))
    return TestClient(create_app(settings, RecordingEvents()), raise_server_exceptions=False)


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
    body = client.get("/v1/items").json()

    assert committed_item.name in [item["name"] for item in body["items"]]
    assert {item["currency"] for item in body["items"]} == {"EUR"}


def test_catalog_pages_through_the_database_by_cursor(client: TestClient, engine: Engine):
    slugs = [f"page-test-{n}" for n in range(2)]
    with Session(engine) as session:
        session.add_all(make_item(name=f"Page {n}", slug=slug) for n, slug in enumerate(slugs))
        session.commit()
    try:
        first = client.get("/v1/items", params={"limit": 1}).json()
        second = client.get("/v1/items", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    finally:
        with Session(engine) as session:
            session.execute(delete(CatalogItem).where(CatalogItem.slug.in_(slugs)))
            session.commit()

    assert [item["slug"] for item in first["items"] + second["items"]] == slugs
    assert second["next_cursor"] is None


def test_catalog_rejects_a_foreign_cursor(client: TestClient):
    response = client.get("/v1/items", params={"cursor": "!!!"})

    assert (response.status_code, response.json()["code"]) == (422, "invalid-cursor")
