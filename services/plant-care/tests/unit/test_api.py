import uuid
from datetime import UTC, date, datetime, timedelta

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
AS_USER = {"X-User-Id": str(USER), "X-User-Groups": "users"}
QUESTIONNAIRE = {
    "lighting": "bright_indirect",
    "dry_depth": "about_2_3cm",
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
    app.state.journal_service = JournalService(lambda: uow, 2, 3, lambda: TODAY)
    app.state.update_service = UpdateService(lambda: uow, quota, 2, 3)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(journals.router)
    app.include_router(updates.router)
    return TestClient(app, raise_server_exceptions=False)


def utc_today():
    return datetime.now(UTC).date()


def update_body(**overrides) -> dict:
    return {"id": str(uuid.uuid4()), **QUESTIONNAIRE, **overrides}


def test_the_user_header_is_required(client: TestClient):
    response = client.get("/v1/journals")

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "unauthorized"


def test_a_malformed_user_header_is_unauthorized(client: TestClient):
    assert client.get("/v1/journals", headers={"X-User-Id": "me"}).status_code == 401


def test_a_missing_journal_carries_the_not_found_code(client: TestClient):
    response = client.get(f"/v1/journals/{uuid.uuid4()}", headers=AS_USER)

    assert (response.status_code, response.json()["code"]) == (404, "not-found")


def test_an_invalid_body_carries_the_validation_error_code(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)

    response = client.put(f"/v1/journals/{journal.id}/planting", json={}, headers=AS_USER)

    assert (response.status_code, response.json()["code"]) == (422, "validation-error")


def test_an_unknown_route_carries_the_not_found_code(client: TestClient):
    assert client.get("/v1/nowhere", headers=AS_USER).json()["code"] == "not-found"


def test_an_unexpected_error_carries_the_internal_error_code(
    client: TestClient, uow: FakeUnitOfWork
):
    uow.journals.page_for_user = None

    response = client.get("/v1/journals", headers=AS_USER)

    assert (response.status_code, response.json()["code"]) == (500, "internal-error")


def test_journals_are_listed_newest_first_for_the_caller_only(
    client: TestClient, uow: FakeUnitOfWork
):
    old = uow.journals.seed(USER)
    new = uow.journals.seed(USER, planted_on=date(2026, 11, 1))
    uow.journals.seed(uuid.uuid4())

    response = client.get("/v1/journals", headers=AS_USER)

    body = response.json()
    assert response.status_code == 200
    assert [journal["id"] for journal in body["items"]] == [str(new.id), str(old.id)]
    assert body["items"][0]["planted_on"] == "2026-11-01"
    assert body["next_cursor"] is None
    assert len(body["items"][0]["checkpoints"]) == 3 and body["items"][1]["checkpoints"] == []


def test_journals_are_paged_with_a_cursor_and_the_limit_is_clamped(
    client: TestClient, uow: FakeUnitOfWork
):
    seeded = [uow.journals.seed(USER) for _ in range(5)]

    first = client.get("/v1/journals", headers=AS_USER).json()
    second = client.get(
        "/v1/journals", params={"cursor": first["next_cursor"], "limit": 100}, headers=AS_USER
    ).json()

    assert [item["id"] for item in first["items"]] == [str(j.id) for j in reversed(seeded[3:])]
    assert [item["id"] for item in second["items"]] == [str(j.id) for j in reversed(seeded[:3])]
    assert second["next_cursor"] is None


def test_a_bad_journals_cursor_is_422_with_its_code(client: TestClient):
    response = client.get("/v1/journals", params={"cursor": "garbage"}, headers=AS_USER)

    assert (response.status_code, response.json()["code"]) == (422, "invalid-cursor")


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


def test_a_future_planting_date_is_422_validation_error(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)

    response = client.put(
        f"/v1/journals/{journal.id}/planting", json={"planted_on": "2026-11-12"}, headers=AS_USER
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation-error"
    assert response.json()["detail"] == "the planting date cannot be in the future"


def test_replanting_on_another_date_is_409_and_on_the_same_date_is_200(
    client: TestClient, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER, planted_on=date(2026, 11, 9))
    url = f"/v1/journals/{journal.id}/planting"

    same = client.put(url, json={"planted_on": "2026-11-09"}, headers=AS_USER)
    other = client.put(url, json={"planted_on": "2026-11-10"}, headers=AS_USER)

    assert same.status_code == 200
    assert other.status_code == 409
    assert other.json()["code"] == "planting-already-recorded"
    assert uow.journals.rows[journal.id].planted_on == date(2026, 11, 9)


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
    body = update_body(sensor_reading="35.5", sensor_unit="vwc_pct")
    url = f"/v1/journals/{journal.id}/updates"

    first = client.post(url, json=body, headers=AS_USER)
    again = client.post(url, json=body, headers=AS_USER)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert first.json()["id"] == body["id"]
    assert first.json()["diagnosis"]["status"] == "diagnosis_pending"
    assert first.json()["escalation"] is None


def test_an_update_with_only_the_lighting_defaults_the_rest(
    client: TestClient, uow: FakeUnitOfWork
):
    journal = uow.journals.seed(USER)
    body = {"id": str(uuid.uuid4()), "lighting": "low"}

    response = client.post(f"/v1/journals/{journal.id}/updates", json=body, headers=AS_USER)

    assert response.status_code == 201
    stored = response.json()
    assert stored["lighting"] == "low"
    for field in (
        "watering_method",
        "pot_diameter_class",
        "drainage_holes",
        "standing_water_over_1h",
        "dry_depth",
        "pot_weight_relative",
    ):
        assert stored[field] == "unknown"
    for field in (
        "last_watered_on",
        "pot_weight_after_watering_g",
        "pot_weight_now_g",
        "repotted_or_moved",
        "sensor_reading",
        "sensor_unit",
        "air_temp_c",
        "air_rh_pct",
        "air_measured_at",
        "air_temp_min_c",
        "air_temp_max_c",
        "air_rh_min_pct",
        "air_rh_max_pct",
    ):
        assert stored[field] is None


def test_every_questionnaire_field_is_stored_and_returned(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)
    body = update_body(
        last_watered_on=TODAY.isoformat(),
        watering_method="through_drainage",
        pot_diameter_class="10_15",
        drainage_holes="yes",
        standing_water_over_1h="no",
        dry_depth="whole_root_ball_dry",
        pot_weight_relative="very_light",
        pot_weight_after_watering_g="820.5",
        pot_weight_now_g="600",
        repotted_or_moved="no",
        sensor_reading="3200",
        sensor_unit="raw",
        air_temp_c="21.5",
        air_rh_pct="48",
        air_measured_at="2026-11-11T09:30:00+02:00",
        air_temp_min_c="-50",
        air_temp_max_c="60",
        air_rh_min_pct="0",
        air_rh_max_pct="100",
    )

    response = client.post(f"/v1/journals/{journal.id}/updates", json=body, headers=AS_USER)

    assert response.status_code == 201
    assert {key: response.json()[key] for key in body} == {
        **body,
        "pot_weight_after_watering_g": "820.5",
        "pot_weight_now_g": "600",
        "sensor_reading": "3200",
        "air_temp_c": "21.5",
        "air_rh_pct": "48",
        "air_temp_min_c": "-50",
        "air_temp_max_c": "60",
        "air_rh_min_pct": "0",
        "air_rh_max_pct": "100",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"sensor_reading": "100", "sensor_unit": "vwc_pct"},
        {"sensor_reading": "0", "sensor_unit": "vwc_pct"},
        {"sensor_reading": "100000", "sensor_unit": "raw"},
        {"pot_weight_now_g": "0.01"},
        {"pot_weight_after_watering_g": "200000"},
        {"air_temp_min_c": "20", "air_temp_max_c": "20"},
        {"air_rh_min_pct": "55", "air_rh_max_pct": "55"},
        {"air_temp_min_c": "20"},
        {"last_watered_on": utc_today().isoformat()},
        {"repotted_or_moved": None},
    ],
)
def test_the_edges_of_the_questionnaire_are_accepted(
    client: TestClient, uow: FakeUnitOfWork, overrides: dict
):
    journal = uow.journals.seed(USER)

    response = client.post(
        f"/v1/journals/{journal.id}/updates", json=update_body(**overrides), headers=AS_USER
    )

    assert response.status_code == 201


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
        {"lighting": "dark"},
        {"watering_method": "sprinkled"},
        {"pot_diameter_class": "huge"},
        {"drainage_holes": "maybe"},
        {"standing_water_over_1h": "maybe"},
        {"dry_depth": "damp"},
        {"pot_weight_relative": "heavier"},
        {"repotted_or_moved": "unknown"},
        {"last_watered_on": (utc_today() + timedelta(days=1)).isoformat()},
        {"pot_weight_now_g": "0"},
        {"pot_weight_now_g": "-5"},
        {"pot_weight_after_watering_g": "200000.01"},
        {"sensor_reading": "-1", "sensor_unit": "raw"},
        {"sensor_reading": "1.234", "sensor_unit": "raw"},
        {"sensor_reading": "35"},
        {"sensor_unit": "raw"},
        {"sensor_reading": "100.01", "sensor_unit": "vwc_pct"},
        {"sensor_reading": "5", "sensor_unit": "pct"},
        {"air_temp_c": "60.01"},
        {"air_temp_c": "-50.01"},
        {"air_rh_pct": "100.01"},
        {"air_rh_pct": "-0.01"},
        {"air_measured_at": "2026-11-11T09:30:00"},
        {"air_temp_min_c": "-51"},
        {"air_temp_max_c": "61"},
        {"air_temp_min_c": "25", "air_temp_max_c": "24.99"},
        {"air_rh_min_pct": "-1"},
        {"air_rh_max_pct": "101"},
        {"air_rh_min_pct": "70", "air_rh_max_pct": "69"},
        {"drainage": "good"},
        {"finger_test": "dry"},
        {"pot_weight": "lighter"},
        {"watering_interval_days": 7},
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
    second = client.get(url, params={"cursor": first["next_cursor"]}, headers=AS_USER).json()

    assert [item["id"] for item in first["items"]] == [str(seeded[2].id), str(seeded[1].id)]
    assert [item["id"] for item in second["items"]] == [str(seeded[0].id)]
    assert second["next_cursor"] is None


def test_a_bad_cursor_and_a_bad_limit_are_422(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(USER)
    url = f"/v1/journals/{journal.id}/updates"

    garbage = client.get(url, params={"cursor": "garbage"}, headers=AS_USER)
    zero = client.get(url, params={"limit": 0}, headers=AS_USER)

    assert (garbage.status_code, garbage.json()["code"]) == (422, "invalid-cursor")
    assert (zero.status_code, zero.json()["code"]) == (422, "validation-error")


def test_the_feed_of_another_users_journal_is_404(client: TestClient, uow: FakeUnitOfWork):
    journal = uow.journals.seed(uuid.uuid4())

    response = client.get(f"/v1/journals/{journal.id}/updates", headers=AS_USER)

    assert response.status_code == 404


def test_the_correlation_id_is_in_the_problem_body(client: TestClient):
    response = client.get("/v1/journals", headers={CORRELATION_HEADER: "corr-1"})

    assert response.json()["correlation_id"] == "corr-1"
