import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from app.main import create_app
from app.service.entitlements import EntitlementService
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
    COMMITTED,
    FakeQueue,
    FakeUnitOfWork,
    make_ai_kind,
    make_expert_kind,
    make_quota,
    make_settings,
    make_tiers,
)

SERVICE = "entitlement"
DESCRIPTION = (
    "Tier and quotas. The tier is computed from the paid orders of the last 30 days and is not "
    "stored; AI and expert quotas are reserved, committed or released through the internal "
    "`/v1/quotas/**` operations that plant-care calls. The gateway route `GET /v1/me/entitlement` "
    "maps to `GET /v1/entitlements/{user_id}`, with the user id taken from the token and never "
    "from the URL. Identity arrives in the `X-User-Id` and `X-User-Groups` headers, which the "
    "API gateway sets from the verified JWT; the internal operations take the user id in the "
    "body. The `internal` tag marks operations the gateway does not expose."
)
PUBLIC_PREFIXES = ("/v1/entitlements",)
USER = uuid.uuid4()


def build_app() -> FastAPI:
    return create_app(make_settings(), FakeQueue(), FakeQueue())


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


def test_only_the_entitlement_read_is_exposed_by_the_gateway():
    document = load_openapi(SERVICE)

    exposure = {
        (method.upper(), path): operation["x-gateway"]
        for path, item in document["paths"].items()
        for method, operation in item.items()
    }
    assert [key for key, value in exposure.items() if value == "public"] == [
        ("GET", "/v1/entitlements/{user_id}")
    ]
    assert "internal" in document["paths"]["/v1/quotas/ai/reservations"]["post"]["tags"]


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def client(uow: FakeUnitOfWork) -> TestClient:
    app = build_app()
    ai, expert = make_ai_kind(starter=2), make_expert_kind(starter=2)
    app.state.ai_quota = make_quota(ai, uow)
    app.state.expert_quota = make_quota(expert, uow)
    app.state.entitlement_service = EntitlementService(lambda: uow, make_tiers(), ai, expert)
    return TestClient(app, raise_server_exceptions=False)


def test_the_entitlement_conforms(client: TestClient, uow: FakeUnitOfWork):
    uow.placed_orders.pay(USER, 4000)

    response = client.get(f"/v1/entitlements/{USER}")

    assert response.status_code == 200 and response.json()["tier"] == "hobbyist"
    assert_conforms(load_openapi(SERVICE), "/v1/entitlements/{user_id}", "GET", response)


def test_an_invalid_user_id_conforms_to_the_problem_schema(client: TestClient):
    response = client.get("/v1/entitlements/not-a-uuid")

    assert response.status_code == 422
    assert_conforms(load_openapi(SERVICE), "/v1/entitlements/{user_id}", "GET", response)


def test_an_ai_reservation_conforms(client: TestClient):
    body = {"reservation_id": str(uuid.uuid4()), "user_id": str(USER)}

    response = client.post("/v1/quotas/ai/reservations", json=body)

    assert response.status_code == 201
    assert_conforms(load_openapi(SERVICE), "/v1/quotas/ai/reservations", "POST", response)


def test_an_exhausted_quota_conforms_to_the_problem_schema(client: TestClient, uow: FakeUnitOfWork):
    uow.expert_reservations.seed(USER, status=COMMITTED)
    uow.expert_reservations.seed(USER, status=COMMITTED)
    body = {"escalation_id": str(uuid.uuid4()), "user_id": str(USER)}

    response = client.post("/v1/quotas/expert/reservations", json=body)

    assert response.status_code == 409 and response.json()["code"] == "quota_exhausted"
    assert_conforms(load_openapi(SERVICE), "/v1/quotas/expert/reservations", "POST", response)


def test_the_conformance_check_rejects_a_body_that_breaks_the_schema():
    response = Response(200, json={"user_id": "not-a-uuid", "tier": "gold"})

    with pytest.raises(AssertionError):
        assert_conforms(load_openapi(SERVICE), "/v1/entitlements/{user_id}", "GET", response)
