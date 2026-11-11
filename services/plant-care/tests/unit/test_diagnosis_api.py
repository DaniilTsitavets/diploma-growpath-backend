import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CorrelationIdMiddleware

from app.api import diagnoses, photos
from app.api.errors import register_error_handlers
from app.main import create_app
from app.service.diagnosis_model import ModelUnavailable
from app.service.model import PhotoKind
from app.service.photos import PhotoService
from tests.unit.fake_model import FakeModel, conclusion, question
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.scenario import USER, Scenario
from tests.unit.settings import make_settings

PROBLEM_JSON = "application/problem+json"
AS_USER = {"X-User-Id": str(USER)}


def client_for(scenario: Scenario, model: FakeModel) -> TestClient:
    app = FastAPI()
    app.state.diagnosis_service = scenario.service(model)
    app.state.photo_service = PhotoService(lambda: scenario.uow, FakePhotoStorage(), 600)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(diagnoses.router)
    app.include_router(photos.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def scenario() -> Scenario:
    return Scenario()


def url(scenario: Scenario, tail: str = "") -> str:
    return f"/v1/updates/{scenario.update_id}/diagnosis{tail}"


def test_running_the_diagnosis_returns_the_completed_resource(scenario: Scenario):
    client = client_for(scenario, FakeModel(conclusion()))

    response = client.post(url(scenario), headers=AS_USER)

    body = response.json()
    assert response.status_code == 200
    assert body["update_id"] == str(scenario.update_id)
    assert body["status"] == "completed"
    assert body["model_effort"] == "high"
    assert body["recommendation"] == "Water less."
    assert [c["rank"] for c in body["candidates"]] == [1, 2]
    assert "ai_reservation_id" not in body


def test_get_returns_the_current_diagnosis(scenario: Scenario):
    client = client_for(scenario, FakeModel(conclusion()))

    body = client.get(url(scenario), headers=AS_USER).json()

    assert body["status"] == "diagnosis_pending"
    assert body["candidates"] == []


def test_the_clarification_flow_end_to_end_over_http(scenario: Scenario):
    client = client_for(scenario, FakeModel(question(roots_photo=True), conclusion(dominant=False)))

    asked = client.post(url(scenario), headers=AS_USER).json()
    answered = client.post(
        url(scenario, "/clarification"), json={"answer": "Yes, soft"}, headers=AS_USER
    ).json()
    declined = client.post(url(scenario, "/escalation-decline"), headers=AS_USER).json()

    assert asked["status"] == "awaiting_clarification"
    assert asked["clarifying_question"] == "Are the leaves soft?"
    assert asked["roots_photo_requested"] is True
    assert answered["status"] == "completed"
    assert answered["escalation_offered"] is True
    assert answered["escalation_declined"] is False
    assert declined["escalation_declined"] is True


def test_a_model_failure_is_a_200_with_a_degraded_diagnosis(scenario: Scenario):
    client = client_for(scenario, FakeModel(ModelUnavailable("down")))

    response = client.post(url(scenario), headers=AS_USER)

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["recommendation"]


def test_an_exhausted_quota_is_409_with_its_code(scenario: Scenario):
    scenario.quota.reserved.clear()
    scenario.quota.exhausted = True

    response = client_for(scenario, FakeModel(conclusion())).post(url(scenario), headers=AS_USER)

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "ai-quota-exhausted"


def test_an_unavailable_quota_service_is_503_problem_json(scenario: Scenario):
    scenario.quota.unavailable = True

    response = client_for(scenario, FakeModel(conclusion())).post(url(scenario), headers=AS_USER)

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON


def test_missing_photos_are_422(scenario: Scenario):
    scenario.uow.photos.rows.clear()

    response = client_for(scenario, FakeModel(conclusion())).post(url(scenario), headers=AS_USER)

    assert response.status_code == 422
    assert "overview" in response.json()["detail"]


def test_a_running_attempt_is_409(scenario: Scenario):
    scenario.seed(model_effort="high")

    response = client_for(scenario, FakeModel(conclusion())).post(url(scenario), headers=AS_USER)

    assert response.status_code == 409
    assert response.json()["code"] == "diagnosis-conflict"


@pytest.mark.parametrize("body", [{}, {"answer": "x", "extra": 1}, {"answer": 5}])
def test_a_malformed_clarification_body_is_422(scenario: Scenario, body: dict):
    scenario.seed(status="awaiting_clarification", model_effort="high", clarifying_question="Q?")

    response = client_for(scenario, FakeModel(conclusion())).post(
        url(scenario, "/clarification"), json=body, headers=AS_USER
    )

    assert response.status_code == 422


def test_a_blank_clarification_is_422_and_a_misplaced_one_is_409(scenario: Scenario):
    client = client_for(scenario, FakeModel(conclusion()))

    misplaced = client.post(
        url(scenario, "/clarification"), json={"answer": "yes"}, headers=AS_USER
    )
    scenario.seed(status="awaiting_clarification", model_effort="high", clarifying_question="Q?")
    blank = client.post(url(scenario, "/clarification"), json={"answer": "  "}, headers=AS_USER)

    assert misplaced.status_code == 409
    assert blank.status_code == 422


def test_declining_without_an_offer_is_409(scenario: Scenario):
    response = client_for(scenario, FakeModel(conclusion())).post(
        url(scenario, "/escalation-decline"), headers=AS_USER
    )

    assert response.status_code == 409


def test_another_users_update_is_404_everywhere(scenario: Scenario):
    client = client_for(scenario, FakeModel(conclusion()))
    stranger = {"X-User-Id": str(uuid.uuid4())}

    responses = [
        client.get(url(scenario), headers=stranger),
        client.post(url(scenario), headers=stranger),
        client.post(url(scenario, "/clarification"), json={"answer": "a"}, headers=stranger),
        client.post(url(scenario, "/escalation-decline"), headers=stranger),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404, 404]


def test_the_user_header_is_required(scenario: Scenario):
    assert client_for(scenario, FakeModel(conclusion())).get(url(scenario)).status_code == 401


def test_a_roots_photo_upload_is_409_until_the_ai_asks_for_one(scenario: Scenario):
    client = client_for(scenario, FakeModel(question(roots_photo=True)))
    upload = f"/v1/updates/{scenario.update_id}/photos"
    roots = {"kind": PhotoKind.ROOTS_DRAINAGE.value}

    before = client.post(upload, json=roots, headers=AS_USER)
    client.post(url(scenario), headers=AS_USER)
    after = client.post(upload, json=roots, headers=AS_USER)
    other = client.post(upload, json={"kind": "symptom_closeup"}, headers=AS_USER)

    assert before.status_code == 409
    assert before.headers["content-type"] == PROBLEM_JSON
    assert before.json()["code"] == "roots-photo-not-requested"
    assert after.status_code == 200
    assert other.status_code == 200


def test_the_application_exposes_the_diagnosis_routes_with_the_mock_model_by_default():
    app = create_app(make_settings(), FakeQueue(), photo_storage=FakePhotoStorage())

    paths = set(app.openapi()["paths"])

    assert {
        "/v1/updates/{update_id}/diagnosis",
        "/v1/updates/{update_id}/diagnosis/clarification",
        "/v1/updates/{update_id}/diagnosis/escalation-decline",
    } <= paths
