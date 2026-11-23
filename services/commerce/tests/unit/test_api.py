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
    def build(*items, declines: bool = False, catalog_down: bool = False) -> TestClient:
        app = create_app(make_settings())
        catalog = FakeCatalog(*items)
        catalog.unavailable = catalog_down
        uow = FakeUnitOfWork(FakeOrders(), catalog)
        app.state.purchase_service = PurchaseService(
            lambda: uow, RecordingPayments(declines), RecordingEvents()
        )
        app.state.catalog_service = CatalogService(
            lambda: uow, default_page_size=3, max_page_size=5
        )
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
    assert response.json()["code"] == "unauthorized"


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
    assert response.json()["code"] == "item-not-found"


def test_key_reused_for_another_item_is_422(make_client):
    item, other = make_item(), make_item(name="Mint Pack")
    client = make_client(item, other)
    client.post("/v1/orders", json={"item_id": str(item.id)}, headers=order_headers())

    response = client.post("/v1/orders", json={"item_id": str(other.id)}, headers=order_headers())

    assert response.status_code == 422
    assert "different item" in response.json()["detail"]
    assert response.json()["code"] == "idempotency-key-reused"


def test_declined_payment_is_402(make_client):
    item = make_item()

    response = make_client(item, declines=True).post(
        "/v1/orders", json={"item_id": str(item.id)}, headers=order_headers()
    )

    assert response.status_code == 402
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "payment-declined"


def test_catalog_lists_active_items_with_labels(make_client):
    kit = make_entry(name="Beginner's Kit", labels=("beginner", "low-maintenance"))
    hidden = make_entry(name="Old Pack", is_active=False)

    response = make_client(kit, hidden).get("/v1/items")

    (item,) = response.json()["items"]
    assert response.status_code == 200
    assert response.json()["next_cursor"] is None
    assert (item["name"], item["labels"], item["price_cents"], item["currency"]) == (
        "Beginner's Kit",
        ["beginner", "low-maintenance"],
        2500,
        "EUR",
    )


def test_catalog_can_be_filtered_by_label(make_client):
    rare = make_entry(name="Chili Pack", labels=("rare",))
    common = make_entry(name="Mint Pack", labels=("beginner",))

    response = make_client(rare, common).get("/v1/items", params={"label": "rare"})

    assert [item["name"] for item in response.json()["items"]] == ["Chili Pack"]


def test_catalog_label_filter_is_validated(make_client):
    response = make_client().get("/v1/items", params={"label": "x" * 41})

    assert response.status_code == 422


def test_catalog_list_carries_slugs_and_is_cacheable(make_client):
    response = make_client(make_entry(slug="golden-pothos", plant_slug="golden-pothos")).get(
        "/v1/items"
    )

    (item,) = response.json()["items"]
    assert (item["slug"], item["plant_slug"]) == ("golden-pothos", "golden-pothos")
    assert response.headers["cache-control"] == "public, max-age=60"


def entries(count: int):
    return [make_entry(slug=f"plant-{n:02d}", name=f"Plant {n}") for n in range(count)]


def slugs(response) -> list[str]:
    return [item["slug"] for item in response.json()["items"]]


def test_catalog_pages_use_the_configured_default_size_and_a_cursor(make_client):
    response = make_client(*entries(4)).get("/v1/items")

    assert slugs(response) == ["plant-00", "plant-01", "plant-02"]
    assert response.json()["next_cursor"] is not None


def test_catalog_cursor_round_trip_walks_every_item_once(make_client):
    client = make_client(*entries(7))
    seen: list[str] = []
    params: dict[str, object] = {"limit": 3}

    while True:
        body = client.get("/v1/items", params=params).json()
        seen += [item["slug"] for item in body["items"]]
        if body["next_cursor"] is None:
            break
        params["cursor"] = body["next_cursor"]

    assert seen == [f"plant-{n:02d}" for n in range(7)]


def test_catalog_last_full_page_has_no_cursor(make_client):
    response = make_client(*entries(3)).get("/v1/items")

    assert len(slugs(response)) == 3
    assert response.json()["next_cursor"] is None


def test_catalog_limit_above_the_maximum_is_clamped(make_client):
    response = make_client(*entries(8)).get("/v1/items", params={"limit": 100})

    assert len(slugs(response)) == 5


@pytest.mark.parametrize("limit", [0, -1, "many"])
def test_catalog_limit_below_one_or_not_a_number_is_422(make_client, limit):
    response = make_client(*entries(2)).get("/v1/items", params={"limit": limit})

    assert response.status_code == 422
    assert response.json()["code"] == "validation-error"


@pytest.mark.parametrize("cursor", ["!!!", "bm90IGEgc2x1Zw%3D%3D", "Tm9wZQ", "X19f"])
def test_catalog_bad_cursor_is_422_invalid_cursor(make_client, cursor):
    response = make_client(*entries(2)).get("/v1/items", params={"cursor": cursor})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "invalid-cursor"
    assert "cache-control" not in response.headers


def test_amounts_are_in_euro(make_client):
    item = make_item()
    client = make_client(item)

    order = client.post("/v1/orders", json={"item_id": str(item.id)}, headers=order_headers())

    assert order.json()["currency"] == "EUR"
    assert client.get("/v1/items/beginners-kit").json()["currency"] == "EUR"


def test_item_is_found_by_slug(make_client):
    entry = make_entry(labels=("beginner",), slug="golden-pothos", plant_slug="golden-pothos")

    response = make_client(entry, make_entry(slug="cactus", plant_slug="cactus")).get(
        "/v1/items/golden-pothos"
    )

    body = response.json()
    assert response.status_code == 200
    assert (body["id"], body["slug"], body["plant_slug"]) == (
        str(entry.id),
        "golden-pothos",
        "golden-pothos",
    )
    assert body["labels"] == ["beginner"]
    assert response.headers["cache-control"] == "public, max-age=60"


def test_sensor_has_no_plant_slug(make_client):
    sensor = make_entry(
        slug="soil-moisture-sensor", item_type="sensor", plant_slug=None, plant_species=None
    )

    response = make_client(sensor).get("/v1/items/soil-moisture-sensor")

    assert response.json()["plant_slug"] is None


@pytest.mark.parametrize(
    "slug", ["unknown", "Upper", "a" * 65, "with_underscore", "dot.dot"], ids=str
)
def test_unknown_or_malformed_slug_is_404_problem(make_client, slug):
    response = make_client(make_entry()).get(f"/v1/items/{slug}")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["title"] == "Item not found"
    assert response.json()["code"] == "item-not-found"
    assert "cache-control" not in response.headers


def test_inactive_item_is_404_by_slug(make_client):
    response = make_client(make_entry(slug="old-pack", is_active=False)).get("/v1/items/old-pack")

    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/v1/items", "/v1/items/golden-pothos"])
def test_catalog_answers_503_with_retry_after_when_the_database_is_down(make_client, path):
    response = make_client(make_entry(), catalog_down=True).get(path)

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["retry-after"] == "5"
    assert response.json()["code"] == "catalog-unavailable"
    assert "cache-control" not in response.headers


@pytest.mark.parametrize("path", ["/v1/items", "/v1/items/golden-pothos"])
def test_an_unreachable_database_is_503_through_the_real_repository(path):
    client = TestClient(create_app(make_settings()), raise_server_exceptions=False)

    response = client.get(path)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_catalog_does_not_need_a_user(make_client):
    response = make_client(make_entry()).get("/v1/items/beginners-kit")

    assert response.status_code == 200


def test_order_for_an_inactive_item_is_404(make_client):
    item = make_item(is_active=False)

    response = make_client(item).post(
        "/v1/orders", json={"item_id": str(item.id)}, headers=order_headers()
    )

    assert response.status_code == 404
