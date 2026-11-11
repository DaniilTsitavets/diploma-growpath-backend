import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CORRELATION_HEADER, CorrelationIdMiddleware

from app.api import journals, updates
from app.api.errors import register_error_handlers
from app.service.journals import JournalService
from app.service.updates import UpdateService
from tests.unit.fakes import TODAY, FakeAiQuota, FakeUnitOfWork

PROBLEM_JSON = "application/problem+json"
USER = uuid.uuid4()
AS_USER = {"X-User-Id": str(USER)}
QUESTIONNAIRE = {
    "drainage": "good",
    "lighting": "bright_indirect",
    "finger_test": "dry",
    "pot_weight": "lighter",
}


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def quota() -> FakeAiQuota:
    return FakeAiQuota()


@pytest.fixture
def client(uow: FakeUnitOfWork, quota: FakeAiQuota) -> TestClient:
    app = FastAPI()
    app.state.journal_service = JournalService(lambda: uow, lambda: TODAY)
    app.state.update_service = UpdateService(lambda: uow, quota, 2, 3)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(journals.router)
    app.include_router(updates.router)
    return TestClient(app, raise_server_exceptions=False)


def update_body(**overrides) -> dict:
    return {"id": str(uuid.uuid4()), **QUESTIONNAIRE, **overrides}


def test_the_user_header_is_required(client: TestClient):
    response = client.get("/v1/journals")

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON


def test_a_malformed_user_header_is_unauthorized(client: TestClient):
    assert client.get("/v1/journals", headers={"X-User-Id": "me"}).status_code == 401


def test_journals_are_listed_newest_first_for_the_caller_only(
    client: TestClient, uow: FakeUnitOfWork
):
    old = uow.journals.seed(USER)
    new = uow.journals.seed(USER, planted_on=date(2026, 11, 1))
    uow.journals.seed(uuid.uuid4())

    response = client.get("/v1/journals", headers=AS_USER)

    body = response.json()
    assert response.status_code == 200
    assert [journal["id"] for journal in body] == [str(new.id), str(old.id)]
    assert body[0]["planted_on"] == "2026-11-01"
    assert len(body[0]["checkpoints"]) == 3 and body[1]["checkpoints"] == []


def test_a_journal_of_another_user_is_404_problem_json(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(uuid.uuid4())

    response = client.get(f"/v1/journals/{journal.id}", headers=AS_USER)

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON


def test_planting_records_the_date_and_returns_the_checkpoints(
    client: TestClient, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER)

    response = client.put(
        f"/v1/journals/{journal.id}/planting", json={"planted_on": "2026-11-09"}, headers=AS_USER
    )

    body = response.json()
    assert response.status_code == 200
    assert body["planted_on"] == "2026-11-09"
    assert [c["state"] for c in body["checkpoints"]] == ["upcoming", "upcoming", "upcoming"]
    assert body["checkpoints"][0]["opens_on"] == "2026-11-14"


@pytest.mark.parametrize(
    "body", [{}, {"planted_on": "yesterday"}, {"planted_on": "2026-11-09", "x": 1}]
)
def test_planting_rejects_a_bad_body(client: TestClient, uow: FakeUnitOfWork, body: dict):
    journal = uow.journals.seed(USER)

    response = client.put(f"/v1/journals/{journal.id}/planting", json=body, headers=AS_USER)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON


def test_an_update_is_created_with_a_pending_diagnosis_then_replayed(
    client: TestClient, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER)
    body = update_body(watering_interval_days=7, sensor_reading="35.5")
    url = f"/v1/journals/{journal.id}/updates"

    first = client.post(url, json=body, headers=AS_USER)
    again = client.post(url, json=body, headers=AS_USER)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert first.json()["id"] == body["id"]
    assert first.json()["diagnosis"]["status"] == "diagnosis_pending"
    assert first.json()["escalation"] is None


def test_an_exhausted_quota_is_409_with_a_code(
    client: TestClient, uow: FakeUnitOfWork, quota: FakeAiQuota
):
    journal = uow.journals.seed(USER)
    quota.exhausted = True

    response = client.post(
        f"/v1/journals/{journal.id}/updates", json=update_body(), headers=AS_USER
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "ai-quota-exhausted"
    assert uow.updates.rows == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"drainage": "ok"},
        {"watering_interval_days": 0},
        {"watering_interval_days": 366},
        {"sensor_reading": "-1"},
        {"sensor_reading": "1.234"},
        {"unknown": 1},
        {"id": "not-a-uuid"},
    ],
)
def test_an_update_outside_the_questionnaire_is_422(
    client: TestClient, uow: FakeUnitOfWork, overrides: dict
):
    journal = uow.journals.seed(USER)

    response = client.post(
        f"/v1/journals/{journal.id}/updates", json=update_body(**overrides), headers=AS_USER
    )

    assert response.status_code == 422
    assert uow.updates.rows == {}


def test_the_feed_is_paged_with_a_cursor(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)
    seeded = [uow.updates.seed(journal.id) for _ in range(3)]
    url = f"/v1/journals/{journal.id}/updates"

    first = client.get(url, headers=AS_USER).json()
    second = client.get(url, params={"before": first["next_cursor"]}, headers=AS_USER).json()

    assert [item["id"] for item in first["items"]] == [str(seeded[2].id), str(seeded[1].id)]
    assert [item["id"] for item in second["items"]] == [str(seeded[0].id)]
    assert second["next_cursor"] is None


def test_a_bad_cursor_and_a_bad_limit_are_422(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)
    url = f"/v1/journals/{journal.id}/updates"

    assert client.get(url, params={"before": "garbage"}, headers=AS_USER).status_code == 422
    assert client.get(url, params={"limit": 0}, headers=AS_USER).status_code == 422


def test_the_feed_of_another_users_journal_is_404(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(uuid.uuid4())

    response = client.get(f"/v1/journals/{journal.id}/updates", headers=AS_USER)

    assert response.status_code == 404


def test_the_correlation_id_is_in_the_problem_body(client: TestClient):
    response = client.get("/v1/journals", headers={CORRELATION_HEADER: "corr-1"})

    assert response.json()["correlation_id"] == "corr-1"
