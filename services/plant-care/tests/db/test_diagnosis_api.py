import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import DiagnosisModel
from app.service.diagnosis_model import ModelUnavailable
from app.service.model import Tier
from tests.db.factories import add_journal
from tests.unit.fake_model import FakeModel, conclusion, question
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import FakeAiQuota
from tests.unit.scenario import StaticTiers
from tests.unit.settings import make_settings

USER = uuid.uuid4()
AS_USER = {"X-User-Id": str(USER)}
QUESTIONNAIRE = {
    "drainage": "poor",
    "lighting": "medium",
    "finger_test": "wet",
    "pot_weight": "heavier",
}


@pytest.fixture
def quota() -> FakeAiQuota:
    return FakeAiQuota()


@pytest.fixture
def journal_id(engine: Engine) -> uuid.UUID:
    with Session(engine) as session:
        values = add_journal(session, user_id=USER)
        session.commit()
    return values["id"]


def client_with(database_url: URL, quota: FakeAiQuota, model: FakeModel) -> TestClient:
    settings = make_settings(database_url=database_url.render_as_string(hide_password=False))
    app = create_app(
        settings,
        FakeQueue(),
        quota,
        FakePhotoStorage(),
        tier_lookup=StaticTiers(Tier.STARTER),
        diagnosis_model=model,
    )
    return TestClient(app, raise_server_exceptions=False)


def update_with_photos(client: TestClient, journal_id: uuid.UUID) -> str:
    created = client.post(
        f"/v1/journals/{journal_id}/updates",
        json={"id": str(uuid.uuid4()), **QUESTIONNAIRE},
        headers=AS_USER,
    )
    update_id = created.json()["id"]
    for kind in ("overview", "soil"):
        client.post(f"/v1/updates/{update_id}/photos", json={"kind": kind}, headers=AS_USER)
    return update_id


def stored_reservation(engine: Engine, update_id: str) -> uuid.UUID:
    with Session(engine) as session:
        return session.scalar(
            select(DiagnosisModel.ai_reservation_id).where(
                DiagnosisModel.update_id == uuid.UUID(update_id)
            )
        )


def test_the_clarification_flow_runs_against_the_database(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID, engine: Engine
):
    client = client_with(
        database_url, quota, FakeModel(question(roots_photo=True), conclusion(dominant=False))
    )
    update_id = update_with_photos(client, journal_id)
    diagnosis = f"/v1/updates/{update_id}/diagnosis"
    photos = f"/v1/updates/{update_id}/photos"
    roots = {"kind": "roots_drainage"}

    roots_before = client.post(photos, json=roots, headers=AS_USER)
    asked = client.post(diagnosis, headers=AS_USER).json()
    roots_after = client.post(photos, json=roots, headers=AS_USER)
    answered = client.post(
        f"{diagnosis}/clarification", json={"answer": "Yes, soft"}, headers=AS_USER
    ).json()
    roots_later = client.post(photos, json=roots, headers=AS_USER)
    declined = client.post(f"{diagnosis}/escalation-decline", headers=AS_USER).json()
    fetched = client.get(diagnosis, headers=AS_USER).json()

    assert roots_before.status_code == 409
    assert asked["status"] == "awaiting_clarification"
    assert asked["model_effort"] == "medium"
    assert roots_after.status_code == 200
    assert answered["status"] == "completed"
    assert answered["escalation_offered"] is True
    assert roots_later.status_code == 409
    assert declined["escalation_declined"] is True
    assert fetched == declined
    assert [c["rank"] for c in fetched["candidates"]] == [1, 2]
    assert quota.committed == [stored_reservation(engine, update_id)]


def test_a_failed_attempt_degrades_and_the_retry_pays_with_a_new_reservation(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID, engine: Engine
):
    client = client_with(database_url, quota, FakeModel(ModelUnavailable("down"), conclusion()))
    update_id = update_with_photos(client, journal_id)
    first_reservation = stored_reservation(engine, update_id)
    diagnosis = f"/v1/updates/{update_id}/diagnosis"

    failed = client.post(diagnosis, headers=AS_USER).json()
    retried = client.post(diagnosis, headers=AS_USER).json()

    new_reservation = stored_reservation(engine, update_id)
    assert (failed["status"], retried["status"]) == ("degraded", "completed")
    assert quota.released == [first_reservation]
    assert new_reservation != first_reservation
    assert quota.committed == [new_reservation]


def test_an_unavailable_entitlement_is_503_with_retry_after_and_changes_nothing(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID, engine: Engine
):
    model = FakeModel(conclusion())
    client = client_with(database_url, quota, model)
    update_id = update_with_photos(client, journal_id)
    before = stored_reservation(engine, update_id)
    quota.unavailable = True

    response = client.post(f"/v1/updates/{update_id}/diagnosis", headers=AS_USER)

    with Session(engine) as session:
        row = session.scalars(select(DiagnosisModel)).one()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(make_settings().unavailable_retry_after_seconds)
    assert response.headers["content-type"] == "application/problem+json"
    assert (row.status, row.model_effort, row.ai_reservation_id) == (
        "diagnosis_pending",
        None,
        before,
    )
    assert model.requests == []


def test_an_attempt_whose_reservation_was_released_is_taken_over_by_the_next_one(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID, engine: Engine
):
    client = client_with(database_url, quota, FakeModel(conclusion()))
    update_id = update_with_photos(client, journal_id)
    crashed = stored_reservation(engine, update_id)
    with Session(engine) as session:
        session.execute(DiagnosisModel.__table__.update().values(model_effort="high"))
        session.commit()
    diagnosis = f"/v1/updates/{update_id}/diagnosis"

    running = client.post(diagnosis, headers=AS_USER)
    quota.unusable.add(crashed)
    taken_over = client.post(diagnosis, headers=AS_USER)

    assert running.status_code == 409
    assert taken_over.status_code == 200
    assert stored_reservation(engine, update_id) != crashed
    assert quota.committed == [stored_reservation(engine, update_id)]


def test_a_diagnosis_cannot_start_without_the_required_photos(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID
):
    client = client_with(database_url, quota, FakeModel(conclusion()))
    created = client.post(
        f"/v1/journals/{journal_id}/updates",
        json={"id": str(uuid.uuid4()), **QUESTIONNAIRE},
        headers=AS_USER,
    ).json()

    response = client.post(f"/v1/updates/{created['id']}/diagnosis", headers=AS_USER)

    assert response.status_code == 422
