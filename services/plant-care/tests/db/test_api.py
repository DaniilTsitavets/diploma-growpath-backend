import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.main import create_app
from tests.db.factories import add_journal
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import FakeAiQuota
from tests.unit.settings import make_settings

USER = uuid.uuid4()
AS_USER = {"X-User-Id": str(USER)}
QUESTIONNAIRE = {
    "drainage": "good",
    "lighting": "bright_indirect",
    "finger_test": "dry",
    "pot_weight": "lighter",
}


@pytest.fixture
def quota() -> FakeAiQuota:
    return FakeAiQuota()


@pytest.fixture
def client(database_url: URL, quota: FakeAiQuota) -> TestClient:
    settings = make_settings(
        database_url=database_url.render_as_string(hide_password=False),
        feed_default_page_size=2,
    )
    app = create_app(settings, FakeQueue(), quota, FakePhotoStorage())
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def journal_id(engine: Engine) -> uuid.UUID:
    with Session(engine) as session:
        values = add_journal(session, user_id=USER)
        session.commit()
    return values["id"]


def submit(client: TestClient, journal_id: uuid.UUID, **overrides):
    body = {"id": str(uuid.uuid4()), **QUESTIONNAIRE, **overrides}
    return client.post(f"/v1/journals/{journal_id}/updates", json=body, headers=AS_USER)


def test_planting_a_journal_computes_checkpoints_on_read(client: TestClient, journal_id):
    put = client.put(
        f"/v1/journals/{journal_id}/planting", json={"planted_on": "2026-11-01"}, headers=AS_USER
    )
    got = client.get(f"/v1/journals/{journal_id}", headers=AS_USER)
    listed = client.get("/v1/journals", headers=AS_USER)

    assert put.status_code == got.status_code == listed.status_code == 200
    assert got.json()["checkpoints"][0]["opens_on"] == "2026-11-06"
    assert listed.json()[0]["id"] == str(journal_id)


def test_an_update_is_idempotent_and_reserves_quota_once(
    client: TestClient, journal_id, quota: FakeAiQuota
):
    body = {"id": str(uuid.uuid4()), **QUESTIONNAIRE, "sensor_reading": "35.50"}
    url = f"/v1/journals/{journal_id}/updates"

    first = client.post(url, json=body, headers=AS_USER)
    again = client.post(url, json=body, headers=AS_USER)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert first.json()["diagnosis"]["status"] == "diagnosis_pending"
    assert len(quota.reserved) == 1


def test_an_exhausted_quota_creates_no_update(client: TestClient, journal_id, quota: FakeAiQuota):
    quota.exhausted = True

    response = submit(client, journal_id)

    assert response.status_code == 409
    assert client.get(f"/v1/journals/{journal_id}/updates", headers=AS_USER).json()["items"] == []


def test_the_feed_is_paged_newest_first_without_repeats(client: TestClient, journal_id):
    ids = [submit(client, journal_id).json()["id"] for _ in range(5)]
    url = f"/v1/journals/{journal_id}/updates"

    seen, cursor = [], None
    while True:
        page = client.get(url, params={"before": cursor} if cursor else {}, headers=AS_USER).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == ids[::-1]


def test_photos_are_recorded_per_kind_and_listed(client: TestClient, journal_id):
    update_id = submit(client, journal_id).json()["id"]
    url = f"/v1/updates/{update_id}/photos"

    client.post(url, json={"kind": "overview"}, headers=AS_USER)
    client.post(url, json={"kind": "overview"}, headers=AS_USER)
    listed = client.get(url, headers=AS_USER).json()

    assert [photo["kind"] for photo in listed["photos"]] == ["overview"]
    assert listed["missing_required"] == ["soil"]


def test_another_user_sees_nothing(client: TestClient, journal_id):
    update_id = submit(client, journal_id).json()["id"]
    stranger = {"X-User-Id": str(uuid.uuid4())}

    assert client.get(f"/v1/journals/{journal_id}", headers=stranger).status_code == 404
    assert client.get(f"/v1/journals/{journal_id}/updates", headers=stranger).status_code == 404
    assert client.get(f"/v1/updates/{update_id}/photos", headers=stranger).status_code == 404
    assert client.get("/v1/journals", headers=stranger).json() == []
