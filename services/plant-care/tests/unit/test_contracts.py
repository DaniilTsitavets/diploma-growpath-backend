import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from app.main import create_app
from app.service.journals import JournalService
from app.service.updates import UpdateService
from tests.unit.contracts import (
    assert_conforms,
    build_openapi,
    load_openapi,
    openapi_path,
    operations_of,
    render_yaml,
    routes_of,
)
from tests.unit.fake_model import FakeModel, conclusion
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import TODAY, FakeAiQuota, FakeUnitOfWork
from tests.unit.scenario import USER, Scenario
from tests.unit.settings import make_settings

SERVICE = "plant-care"
DESCRIPTION = (
    "Plant journals and their updates, AI diagnosis, escalation to an expert and the care chat. "
    "Consumes `order.placed` and `ticket.*`, publishes `escalation.requested`. Identity arrives "
    "in the `X-User-Id` header, which the API gateway sets from the verified JWT. The gateway "
    "exposes `/v1/journals/**` and `/v1/updates/**`; the `internal` tag marks operations it "
    "does not expose."
)
PUBLIC_PREFIXES = ("/v1/journals", "/v1/updates")
AS_USER = {"X-User-Id": str(USER)}
QUESTIONNAIRE = {
    "drainage": "good",
    "lighting": "bright_indirect",
    "finger_test": "dry",
    "pot_weight": "lighter",
}


def build_app() -> FastAPI:
    return create_app(
        make_settings(), FakeQueue(), photo_storage=FakePhotoStorage(), ticket_queue=FakeQueue()
    )


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


def test_every_business_operation_is_public_and_health_is_internal():
    document = load_openapi(SERVICE)

    exposure = {
        path: operation["x-gateway"]
        for path, item in document["paths"].items()
        for operation in item.values()
    }
    assert {path for path, value in exposure.items() if value == "internal"} == {
        "/health/live",
        "/health/ready",
    }


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def journal_client(uow: FakeUnitOfWork) -> TestClient:
    app = build_app()
    app.state.journal_service = JournalService(lambda: uow, lambda: TODAY)
    app.state.update_service = UpdateService(lambda: uow, FakeAiQuota(), 2, 3)
    return TestClient(app, raise_server_exceptions=False)


def test_the_journal_list_conforms(journal_client: TestClient, uow: FakeUnitOfWork):
    uow.journals.seed(USER)

    response = journal_client.get("/v1/journals", headers=AS_USER)

    assert response.status_code == 200 and response.json()
    assert_conforms(load_openapi(SERVICE), "/v1/journals", "GET", response)


def test_a_missing_journal_conforms_to_the_problem_schema(journal_client: TestClient):
    response = journal_client.get(f"/v1/journals/{uuid.uuid4()}", headers=AS_USER)

    assert response.status_code == 404
    assert_conforms(load_openapi(SERVICE), "/v1/journals/{journal_id}", "GET", response)


def test_a_created_update_conforms(journal_client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)

    response = journal_client.post(
        f"/v1/journals/{journal.id}/updates",
        json={"id": str(uuid.uuid4()), **QUESTIONNAIRE},
        headers=AS_USER,
    )

    assert response.status_code == 201
    assert_conforms(load_openapi(SERVICE), "/v1/journals/{journal_id}/updates", "POST", response)


def test_a_completed_diagnosis_conforms():
    scenario = Scenario()
    app = build_app()
    app.state.diagnosis_service = scenario.service(FakeModel(conclusion()))

    response = TestClient(app).post(f"/v1/updates/{scenario.update_id}/diagnosis", headers=AS_USER)

    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert_conforms(load_openapi(SERVICE), "/v1/updates/{update_id}/diagnosis", "POST", response)


def test_the_conformance_check_rejects_a_body_that_breaks_the_schema():
    response = Response(200, json=[{"id": "not-a-uuid"}])

    with pytest.raises(AssertionError):
        assert_conforms(load_openapi(SERVICE), "/v1/journals", "GET", response)
