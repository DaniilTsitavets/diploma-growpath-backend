import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from app.main import create_app
from app.service.expert_tickets import ExpertTicketService
from app.service.ticket_publisher import TerminalEventPublisher
from app.service.tickets import TicketService
from tests.unit.contracts import (
    assert_conforms,
    build_openapi,
    load_openapi,
    openapi_path,
    operations_of,
    render_yaml,
    routes_of,
)
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import TTL_SECONDS, FakeUnitOfWork, RecordingEvents, make_ticket
from tests.unit.settings import make_settings

SERVICE = "expert-consultation"
DESCRIPTION = (
    "Expert tickets: a state machine open, then answered or cancelled (cancelled only by expiry), "
    "the context snapshot and the expert answer. Consumes `escalation.requested`, publishes "
    "`ticket.created`, `ticket.answered` and `ticket.cancelled`. Identity arrives in the "
    "`X-User-Id` and `X-User-Groups` headers, which the API gateway sets from the verified JWT; "
    "every `/v1/expert/**` operation requires the `experts` group. The `internal` tag marks "
    "operations the gateway does not expose."
)
PUBLIC_PREFIXES = ("/v1/expert/tickets",)
EXPERT = uuid.uuid4()
AS_EXPERT = {"X-User-Id": str(EXPERT), "X-User-Groups": "experts"}


def build_app() -> FastAPI:
    return create_app(make_settings(), FakeQueue(), RecordingEvents(), FakePhotoStorage())


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


def test_the_ticket_operations_are_public_and_health_is_internal():
    document = load_openapi(SERVICE)

    exposure = {
        path: operation["x-gateway"]
        for path, item in document["paths"].items()
        for operation in item.values()
    }
    assert {path for path, value in exposure.items() if value == "public"} == {
        "/v1/expert/tickets",
        "/v1/expert/tickets/{ticket_id}",
        "/v1/expert/tickets/{ticket_id}/answer",
    }
    assert "internal" in document["paths"]["/health/ready"]["get"]["tags"]


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def client(uow: FakeUnitOfWork) -> TestClient:
    app = build_app()
    events = RecordingEvents(uow.log)
    app.state.ticket_service = TicketService(
        lambda: uow, events, TerminalEventPublisher(lambda: uow, events), TTL_SECONDS
    )
    app.state.expert_ticket_service = ExpertTicketService(lambda: uow, FakePhotoStorage(), 2, 3)
    return TestClient(app, raise_server_exceptions=False)


def test_the_ticket_queue_conforms(client: TestClient, uow: FakeUnitOfWork):
    uow.tickets.add(make_ticket())

    response = client.get("/v1/expert/tickets", headers=AS_EXPERT)

    assert response.status_code == 200 and response.json()["items"]
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)


def test_a_ticket_with_its_context_conforms(client: TestClient, uow: FakeUnitOfWork):
    ticket = uow.tickets.add(make_ticket())

    response = client.get(f"/v1/expert/tickets/{ticket.id}", headers=AS_EXPERT)

    assert response.status_code == 200
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets/{ticket_id}", "GET", response)


def test_an_answer_conforms(client: TestClient, uow: FakeUnitOfWork):
    ticket = uow.tickets.add(make_ticket())

    response = client.post(
        f"/v1/expert/tickets/{ticket.id}/answer", json={"body": "Water less."}, headers=AS_EXPERT
    )

    assert response.status_code == 201
    assert_conforms(
        load_openapi(SERVICE), "/v1/expert/tickets/{ticket_id}/answer", "POST", response
    )


def test_a_caller_outside_the_experts_group_conforms_to_the_problem_schema(client: TestClient):
    headers = {"X-User-Id": str(EXPERT), "X-User-Groups": "users"}

    response = client.get("/v1/expert/tickets", headers=headers)

    assert response.status_code == 403
    assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)


def test_the_conformance_check_rejects_a_body_that_breaks_the_schema():
    response = Response(200, json={"items": "none"})

    with pytest.raises(AssertionError):
        assert_conforms(load_openapi(SERVICE), "/v1/expert/tickets", "GET", response)
