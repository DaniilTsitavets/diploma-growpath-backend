import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CorrelationIdMiddleware

from app.api import escalations
from app.api.errors import register_error_handlers
from app.service.escalations import EscalationService
from app.service.model import EscalationStatus
from tests.unit.test_escalation_service import USER, Setup

AS_USER = {"X-User-Id": str(USER)}
RETRY_AFTER_SECONDS = 7


def client_for(setup: Setup) -> TestClient:
    app = FastAPI()
    app.state.escalation_service = EscalationService(
        lambda: setup.uow, setup.quota, setup.announcer
    )
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app, RETRY_AFTER_SECONDS)
    app.include_router(escalations.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def setup() -> Setup:
    return Setup()


def url(setup: Setup) -> str:
    return f"/v1/updates/{setup.update_id}/escalation"


def test_an_offered_escalation_is_created_without_a_body(setup: Setup):
    response = client_for(setup).post(url(setup), headers=AS_USER)

    body = response.json()
    assert response.status_code == 201
    assert body["status"] == "requested"
    assert body["update_id"] == str(setup.update_id)
    assert body["user_question"] is None
    assert body["answer_text"] is None


def test_a_manual_escalation_carries_the_question(setup: Setup):
    response = client_for(setup).post(url(setup), json={"user_question": "Why?"}, headers=AS_USER)

    assert response.status_code == 201
    assert response.json()["user_question"] == "Why?"


def test_a_repeated_request_answers_200_with_the_same_escalation(setup: Setup):
    client = client_for(setup)
    first = client.post(url(setup), headers=AS_USER).json()

    second = client.post(url(setup), headers=AS_USER)

    assert second.status_code == 200
    assert second.json()["id"] == first["id"]


def test_get_returns_the_escalation_with_the_answer(setup: Setup):
    client = client_for(setup)
    created = client.post(url(setup), headers=AS_USER).json()
    setup.uow.escalations.change(
        uuid.UUID(created["id"]), status=EscalationStatus.ANSWERED, answer_text="Repot it."
    )

    body = client.get(url(setup), headers=AS_USER).json()

    assert body["status"] == "answered"
    assert body["answer_text"] == "Repot it."


def test_a_missing_offer_is_409():
    setup = Setup(escalation_offered=False)

    response = client_for(setup).post(url(setup), headers=AS_USER)

    assert response.status_code == 409
    assert response.json()["code"] == "escalation-not-offered"


def test_an_exhausted_quota_is_409(setup: Setup):
    setup.quota.exhausted = True

    response = client_for(setup).post(url(setup), headers=AS_USER)

    assert response.status_code == 409
    assert response.json()["code"] == "expert-quota-exhausted"


def test_an_unavailable_quota_is_503_with_retry_after(setup: Setup):
    setup.quota.reserve_unavailable = True

    response = client_for(setup).post(url(setup), headers=AS_USER)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)


def test_an_unusable_question_is_422(setup: Setup):
    response = client_for(setup).post(url(setup), json={"user_question": "  "}, headers=AS_USER)

    assert response.status_code == 422


def test_unknown_body_fields_are_rejected(setup: Setup):
    response = client_for(setup).post(url(setup), json={"question": "x"}, headers=AS_USER)

    assert response.status_code == 422


def test_a_foreign_update_and_a_missing_escalation_are_404(setup: Setup):
    client = client_for(setup)

    assert client.post(url(setup), headers={"X-User-Id": str(uuid.uuid4())}).status_code == 404
    assert client.get(url(setup), headers=AS_USER).status_code == 404


def test_the_user_header_is_required(setup: Setup):
    assert client_for(setup).get(url(setup)).status_code == 401
