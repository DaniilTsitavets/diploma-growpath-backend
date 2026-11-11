import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service.catalog import CatalogService
from app.service.purchase import PurchaseService
from tests.unit.fakes import (
    FakeCatalog,
    FakeOrders,
    FakeUnitOfWork,
    RecordingEvents,
    RecordingPayments,
    make_entry,
    make_item,
    make_settings,
)

PROBLEM_JSON = "application/problem+json"
USER = str(uuid.uuid4())


@pytest.fixture
def make_client() -> Callable[..., TestClient]:
    def build(*items, declines: bool = False) -> TestClient:
        app = create_app(make_settings())
        uow = FakeUnitOfWork(FakeOrders(), FakeCatalog(*items))
        app.state.purchase_service = PurchaseService(
            lambda: uow, RecordingPayments(declines), RecordingEvents()
        )
        app.state.catalog_service = CatalogService(lambda: uow)
        return TestClient(app, raise_server_exceptions=False)

    return build


def order_headers(key: str = "key-1", user: str | None = USER) -> dict[str, str]:
    headers = {"Idempotency-Key": key}
    if user is not None:
        headers["X-User-Id"] = user
    return headers


def test_order_is_created_with_the_first_purchase_discount(make_client):
    item = make_item()

    response = make_client(item).post(
        "/v1/orders", json={"item_id": str(item.id)}, headers=order_headers()
    )

    body = response.json()
    assert response.status_code == 201
    assert body["item_id"] == str(item.id)
    assert (body["list_price_cents"], body["discount_cents"], body["total_paid_cents"]) == (
        2500,
        500,
        2000,
    )
    assert "user_id" not in body


def test_repeated_request_returns_the_same_order_with_200(make_client):
    item = make_item()
    client = make_client(item)
    request = {"json": {"item_id": str(item.id)}, "headers": order_headers()}

    first = client.post("/v1/orders", **request)
    repeated = client.post("/v1/orders", **request)

    assert (first.status_code, repeated.status_code) == (201, 200)
    assert repeated.json() == first.json()


@pytest.mark.parametrize("user", [None, "not-a-uuid"], ids=["missing user", "malformed user"])
def test_order_requires_an_identified_user(make_client, user):
    item = make_item()

    response = make_client(item).post(
        "/v1/orders", json={"item_id": str(item.id)}, headers=order_headers(user=user)
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"X-User-Id": USER}, {"item_id": str(uuid.uuid4())}),
        (order_headers(key="k" * 129), {"item_id": str(uuid.uuid4())}),
        (order_headers(key=""), {"item_id": str(uuid.uuid4())}),
        (order_headers(), {"item_id": "not-a-uuid"}),
        (order_headers(), {}),
    ],
    ids=["missing key", "key of 129 characters", "empty key", "malformed item id", "no item id"],
)
def test_order_request_must_match_the_contract(make_client, headers, body):
    response = make_client().post("/v1/orders", json=body, headers=headers)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON


def test_unknown_item_is_404(make_client):
    response = make_client().post(
        "/v1/orders", json={"item_id": str(uuid.uuid4())}, headers=order_headers()
    )

    assert response.status_code == 404
    assert response.json()["title"] == "Not Found"


def test_key_reused_for_another_item_is_422(make_client):
    item, other = make_item(), make_item(name="Mint Pack")
    client = make_client(item, other)
    client.post("/v1/orders", json={"item_id": str(item.id)}, headers=order_headers())

    response = client.post("/v1/orders", json={"item_id": str(other.id)}, headers=order_headers())

    assert response.status_code == 422
    assert "different item" in response.json()["detail"]


def test_declined_payment_is_402(make_client):
    item = make_item()

    response = make_client(item, declines=True).post(
        "/v1/orders", json={"item_id": str(item.id)}, headers=order_headers()
    )

    assert response.status_code == 402
    assert response.headers["content-type"] == PROBLEM_JSON


def test_catalog_lists_active_items_with_labels(make_client):
    kit = make_entry(name="Beginner's Kit", labels=("beginner", "low-maintenance"))
    hidden = make_entry(name="Old Pack", is_active=False)

    response = make_client(kit, hidden).get("/v1/items")

    (item,) = response.json()
    assert response.status_code == 200
    assert (item["name"], item["labels"], item["price_cents"]) == (
        "Beginner's Kit",
        ["beginner", "low-maintenance"],
        2500,
    )


def test_catalog_can_be_filtered_by_label(make_client):
    rare = make_entry(name="Chili Pack", labels=("rare",))
    common = make_entry(name="Mint Pack", labels=("beginner",))

    response = make_client(rare, common).get("/v1/items", params={"label": "rare"})

    assert [item["name"] for item in response.json()] == ["Chili Pack"]


def test_catalog_label_filter_is_validated(make_client):
    response = make_client().get("/v1/items", params={"label": "x" * 41})

    assert response.status_code == 422
