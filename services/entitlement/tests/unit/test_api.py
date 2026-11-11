import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service.entitlements import EntitlementService
from tests.unit.fakes import (
    COMMITTED,
    NOW,
    RESERVED,
    FakeUnitOfWork,
    make_ai_kind,
    make_expert_kind,
    make_quota,
    make_settings,
    make_tiers,
)

PROBLEM_JSON = "application/problem+json"
USER = uuid.uuid4()

KINDS = pytest.mark.parametrize(
    ("kind", "id_field"), [("ai", "reservation_id"), ("expert", "escalation_id")]
)


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def client(uow: FakeUnitOfWork) -> TestClient:
    app = create_app(make_settings(lock_retry_after_seconds=3))
    ai, expert = make_ai_kind(starter=2), make_expert_kind(starter=2)
    app.state.ai_quota = make_quota(ai, uow)
    app.state.expert_quota = make_quota(expert, uow)
    app.state.entitlement_service = EntitlementService(lambda: uow, make_tiers(), ai, expert)
    return TestClient(app, raise_server_exceptions=False)


def reservations(uow: FakeUnitOfWork, kind: str):
    return uow.ai_reservations if kind == "ai" else uow.expert_reservations


def url(kind: str, suffix: str = "") -> str:
    return f"/v1/quotas/{kind}/reservations{suffix}"


@KINDS
def test_reserve_creates_then_replays(client: TestClient, kind: str, id_field: str):
    body = {id_field: str(uuid.uuid4()), "user_id": str(USER)}

    first = client.post(url(kind), json=body)
    repeated = client.post(url(kind), json=body)

    assert (first.status_code, repeated.status_code) == (201, 200)
    assert repeated.json() == first.json()
    assert first.json()[id_field] == body[id_field]
    assert first.json()["status"] == "reserved"
    assert "user_id" not in first.json()


@KINDS
def test_reserve_at_the_ceiling_is_409_with_a_code(
    client: TestClient, uow: FakeUnitOfWork, kind: str, id_field: str
):
    reservations(uow, kind).seed(USER, status=COMMITTED)
    reservations(uow, kind).seed(USER, status=COMMITTED)

    response = client.post(url(kind), json={id_field: str(uuid.uuid4()), "user_id": str(USER)})

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "quota_exhausted"


@KINDS
def test_reserve_with_an_id_of_another_user_is_409(
    client: TestClient, uow: FakeUnitOfWork, kind: str, id_field: str
):
    existing = reservations(uow, kind).seed(uuid.uuid4())

    response = client.post(url(kind), json={id_field: str(existing.id), "user_id": str(USER)})

    assert response.status_code == 409
    assert response.json()["code"] == "reservation_id_in_use"
    assert str(existing.user_id) not in response.text


@KINDS
def test_reserve_during_a_lock_timeout_is_503_with_retry_after(
    client: TestClient, uow: FakeUnitOfWork, kind: str, id_field: str
):
    reservations(uow, kind).lock_times_out = True

    response = client.post(url(kind), json={id_field: str(uuid.uuid4()), "user_id": str(USER)})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["title"] == "Service Unavailable"


@KINDS
@pytest.mark.parametrize("action", ["commit", "release"])
def test_commit_and_release_answer_with_the_new_status(
    client: TestClient, uow: FakeUnitOfWork, kind: str, id_field: str, action: str
):
    existing = reservations(uow, kind).seed(USER)

    response = client.post(url(kind, f"/{existing.id}/{action}"), json={"user_id": str(USER)})

    assert response.status_code == 200
    assert response.json()[id_field] == str(existing.id)
    assert response.json()["status"] == {"commit": "committed", "release": "released"}[action]


@KINDS
@pytest.mark.parametrize("action", ["commit", "release"])
def test_commit_and_release_of_a_foreign_reservation_are_404(
    client: TestClient, uow: FakeUnitOfWork, kind: str, id_field: str, action: str
):
    existing = reservations(uow, kind).seed(uuid.uuid4())

    response = client.post(url(kind, f"/{existing.id}/{action}"), json={"user_id": str(USER)})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert str(existing.user_id) not in response.text


def test_commit_of_an_expired_ai_reservation_is_409(client: TestClient, uow: FakeUnitOfWork):
    existing = uow.ai_reservations.seed(USER, status=RESERVED, unexpired=False)

    response = client.post(url("ai", f"/{existing.id}/commit"), json={"user_id": str(USER)})

    assert response.status_code == 409
    assert response.json()["code"] == "reservation_unusable"


@KINDS
def test_rest_release_of_a_committed_reservation_is_409(
    client: TestClient, uow: FakeUnitOfWork, kind: str, id_field: str
):
    existing = reservations(uow, kind).seed(USER, status=COMMITTED)

    response = client.post(url(kind, f"/{existing.id}/release"), json={"user_id": str(USER)})

    assert response.status_code == 409
    assert response.json()["code"] == "reservation_unusable"
    assert reservations(uow, kind).rows[existing.id].status is COMMITTED


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (url("ai"), {"user_id": str(USER)}),
        (url("ai"), {"reservation_id": "nope", "user_id": str(USER)}),
        (url("expert"), {"reservation_id": str(uuid.uuid4()), "user_id": str(USER)}),
        (url("ai"), {"reservation_id": str(uuid.uuid4())}),
        (url("ai", f"/{uuid.uuid4()}/commit"), {}),
        (url("ai", "/not-a-uuid/commit"), {"user_id": str(USER)}),
    ],
    ids=[
        "no id",
        "malformed id",
        "wrong key name",
        "no user",
        "commit without user",
        "bad path id",
    ],
)
def test_requests_must_match_the_contract(client: TestClient, path: str, body: dict):
    response = client.post(path, json=body)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON


def test_entitlement_reports_tier_and_quotas(client: TestClient, uow: FakeUnitOfWork):
    uow.placed_orders.pay(USER, 4000)
    uow.placed_orders.tier_expiry = NOW
    uow.ai_reservations.seed(USER, status=COMMITTED)

    response = client.get(f"/v1/entitlements/{USER}")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(USER),
        "tier": "hobbyist",
        "tier_expires_at": "2026-11-11T12:00:00Z",
        "ai_quota_today": {"used": 1, "limit": 10},
        "expert_quota_30_days": {"used": 0, "limit": 2},
    }


def test_entitlement_of_a_new_user_is_starter_without_end_date(client: TestClient):
    response = client.get(f"/v1/entitlements/{uuid.uuid4()}")

    assert response.json()["tier"] == "starter"
    assert response.json()["tier_expires_at"] is None


def test_entitlement_requires_a_valid_user_id(client: TestClient):
    response = client.get("/v1/entitlements/not-a-uuid")

    assert response.status_code == 422


def test_unexpected_failure_is_500_without_internals(client: TestClient, uow: FakeUnitOfWork):
    def broken(*args: object) -> None:
        raise RuntimeError("secret internals")

    uow.ai_reservations.lock_user = broken
    response = client.post(
        url("ai"), json={"reservation_id": str(uuid.uuid4()), "user_id": str(USER)}
    )

    assert response.status_code == 500
    assert "secret internals" not in response.text
