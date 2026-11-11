import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.engine import URL

from app.db import create_db_engine
from app.main import create_app
from tests.unit.fakes import make_settings

HOLD_LOCK = text(
    "SELECT pg_advisory_xact_lock(hashtextextended('ai:' || CAST(:user_id AS text), 0))"
)


def settings_for(database_url: URL, **overrides):
    return make_settings(
        database_url=database_url.render_as_string(hide_password=False), **overrides
    )


@pytest.fixture
def client(database_url: URL) -> TestClient:
    return TestClient(create_app(settings_for(database_url)), raise_server_exceptions=False)


def test_connections_use_utc_whatever_the_server_says(database_url: URL):
    engine = create_db_engine(settings_for(database_url))

    with engine.connect() as connection:
        assert connection.execute(text("SHOW timezone")).scalar_one() == "UTC"
    engine.dispose()


def pay(engine: Engine, user_id: uuid.UUID, cents: int, days_ago: int = 5) -> datetime:
    placed_at = datetime.now(UTC) - timedelta(days=days_ago)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO placed_orders (order_id, user_id, total_paid_cents, placed_at)"
                " VALUES (:id, :user_id, :cents, :placed_at)"
            ),
            {"id": uuid.uuid4(), "user_id": user_id, "cents": cents, "placed_at": placed_at},
        )
    return placed_at


def test_ai_reservation_flow_over_http(client: TestClient):
    user_id, reservation_id = uuid.uuid4(), uuid.uuid4()
    body = {"reservation_id": str(reservation_id), "user_id": str(user_id)}
    base = "/v1/quotas/ai/reservations"

    reserved = client.post(base, json=body)
    replayed = client.post(base, json=body)
    committed = client.post(f"{base}/{reservation_id}/commit", json={"user_id": str(user_id)})
    entitlement = client.get(f"/v1/entitlements/{user_id}").json()

    assert (reserved.status_code, replayed.status_code, committed.status_code) == (201, 200, 200)
    assert committed.json()["status"] == "committed"
    assert entitlement["ai_quota_today"] == {"used": 1, "limit": 3}


def test_expert_reservation_flow_over_http(client: TestClient, engine: Engine):
    user_id, escalation_id = uuid.uuid4(), uuid.uuid4()
    pay(engine, user_id, 4000)
    base = "/v1/quotas/expert/reservations"

    reserved = client.post(
        base, json={"escalation_id": str(escalation_id), "user_id": str(user_id)}
    )
    released = client.post(f"{base}/{escalation_id}/release", json={"user_id": str(user_id)})
    entitlement = client.get(f"/v1/entitlements/{user_id}").json()

    assert (reserved.status_code, released.status_code) == (201, 200)
    assert released.json()["status"] == "released"
    assert entitlement["expert_quota_30_days"] == {"used": 0, "limit": 2}


def test_a_starter_has_no_expert_quota(client: TestClient):
    response = client.post(
        "/v1/quotas/expert/reservations",
        json={"escalation_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "quota_exhausted"


def test_entitlement_is_computed_from_placed_orders(client: TestClient, engine: Engine):
    user_id = uuid.uuid4()
    placed_at = pay(engine, user_id, 9500)

    body = client.get(f"/v1/entitlements/{user_id}").json()

    assert body["tier"] == "premium"
    assert body["ai_quota_today"]["limit"] == 30
    assert datetime.fromisoformat(body["tier_expires_at"]) == placed_at + timedelta(days=30)


def test_a_busy_user_lock_is_503_with_retry_after(database_url: URL, engine: Engine):
    app = create_app(
        settings_for(database_url, lock_timeout_seconds=0.2, lock_retry_after_seconds=2)
    )
    user_id = uuid.uuid4()

    with engine.connect() as holder:
        holder.execute(HOLD_LOCK, {"user_id": user_id})

        response = TestClient(app, raise_server_exceptions=False).post(
            "/v1/quotas/ai/reservations",
            json={"reservation_id": str(uuid.uuid4()), "user_id": str(user_id)},
        )

        holder.rollback()

    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.headers["content-type"] == "application/problem+json"
