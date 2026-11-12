import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from app.main import create_app
from app.service.catalog import CatalogService
from app.service.purchase import PurchaseService
from tests.unit.contracts import (
    assert_conforms,
    build_openapi,
    load_openapi,
    openapi_path,
    operations_of,
    render_yaml,
    routes_of,
)
from tests.unit.fakes import (
    FakeCatalog,
    FakeOrders,
    FakeUnitOfWork,
    RecordingEvents,
    RecordingPayments,
    make_item,
    make_settings,
)

SERVICE = "commerce"
DESCRIPTION = (
    "Catalog, orders, the mock payment and the first-purchase discount. "
    "Publishes `order.placed`. "
    "Identity arrives in the `X-User-Id` header, which the API gateway sets from the verified "
    "JWT. The `internal` tag marks operations the gateway does not expose."
)
PUBLIC_PREFIXES = ("/v1/items", "/v1/orders")
USER = str(uuid.uuid4())


def build_app() -> FastAPI:
    return create_app(make_settings(), RecordingEvents())


def test_the_committed_openapi_is_what_the_app_generates():
    expected = render_yaml(build_openapi(build_app(), DESCRIPTION, PUBLIC_PREFIXES))
    path = openapi_path(SERVICE)
    if os.environ.get("UPDATE_CONTRACTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        return

    assert path.read_text(encoding="utf-8") == expected, (
        f"{path.name} is out of date with the app: run `make contracts`"
    )


def test_every_route_is_in_the_contract_and_the_other_way_round():
    documented = operations_of(load_openapi(SERVICE))

    assert routes_of(build_app()) == documented


def test_the_openapi_document_is_valid():
    validator = pytest.importorskip("openapi_spec_validator")

    validator.validate(load_openapi(SERVICE))


def test_every_operation_declares_its_gateway_exposure():
    document = load_openapi(SERVICE)

    exposure = {
        (method.upper(), path): operation["x-gateway"]
        for path, item in document["paths"].items()
        for method, operation in item.items()
    }
    assert exposure[("POST", "/v1/orders")] == "public"
    assert exposure[("GET", "/v1/items")] == "public"
    assert exposure[("GET", "/health/ready")] == "internal"
    assert "internal" in document["paths"]["/health/live"]["get"]["tags"]


@pytest.fixture
def client():
    def build(*items, declines: bool = False) -> TestClient:
        app = build_app()
        uow = FakeUnitOfWork(FakeOrders(), FakeCatalog(*items))
        app.state.purchase_service = PurchaseService(
            lambda: uow, RecordingPayments(declines), RecordingEvents()
        )
        app.state.catalog_service = CatalogService(lambda: uow)
        return TestClient(app, raise_server_exceptions=False)

    return build


def place(client: TestClient, item_id: uuid.UUID):
    return client.post(
        "/v1/orders",
        json={"item_id": str(item_id)},
        headers={"Idempotency-Key": "key-1", "X-User-Id": USER},
    )


def test_the_item_list_conforms(client):
    response = client(make_item()).get("/v1/items")

    assert response.status_code == 200 and response.json()
    assert_conforms(load_openapi(SERVICE), "/v1/items", "GET", response)


def test_a_created_order_conforms(client):
    item = make_item()

    response = place(client(item), item.id)

    assert response.status_code == 201
    assert_conforms(load_openapi(SERVICE), "/v1/orders", "POST", response)


def test_a_declined_payment_conforms_to_the_problem_schema(client):
    item = make_item()

    response = place(client(item, declines=True), item.id)

    assert response.status_code == 402
    assert_conforms(load_openapi(SERVICE), "/v1/orders", "POST", response)


def test_the_conformance_check_rejects_a_body_that_breaks_the_schema():
    response = Response(201, json={"id": "not-a-uuid"})

    with pytest.raises(AssertionError):
        assert_conforms(load_openapi(SERVICE), "/v1/orders", "POST", response)


def test_a_validation_error_conforms_to_the_problem_schema(client):
    response = client().post("/v1/orders", json={}, headers={"X-User-Id": USER})

    assert response.status_code == 422
    assert_conforms(load_openapi(SERVICE), "/v1/orders", "POST", response)
