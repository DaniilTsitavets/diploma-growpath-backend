import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import UpdatePhotoModel
from tests.db.factories import add_journal
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage, upload_photo
from tests.unit.fakes import FakeAiQuota
from tests.unit.settings import make_settings

USER = uuid.uuid4()
AS_USER = {"X-User-Id": str(USER), "X-User-Groups": "users"}
QUESTIONNAIRE = {
    "lighting": "bright_indirect",
    "dry_depth": "about_2_3cm",
}


@pytest.fixture
def quota() -> FakeAiQuota:
    return FakeAiQuota()


@pytest.fixture
def storage() -> FakePhotoStorage:
    return FakePhotoStorage()


@pytest.fixture
def client(database_url: URL, quota: FakeAiQuota, storage: FakePhotoStorage) -> TestClient:
    settings = make_settings(
        database_url=database_url.render_as_string(hide_password=False),
        feed_default_page_size=2,
    )
    app = create_app(settings, FakeQueue(), quota, storage)
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
    assert [item["id"] for item in listed.json()["items"]] == [str(journal_id)]
    assert listed.json()["next_cursor"] is None


def test_planting_rules_over_the_real_database(client: TestClient, journal_id):
    url = f"/v1/journals/{journal_id}/planting"

    future = client.put(url, json={"planted_on": "2999-01-01"}, headers=AS_USER)
    first = client.put(url, json={"planted_on": "2026-11-01"}, headers=AS_USER)
    same = client.put(url, json={"planted_on": "2026-11-01"}, headers=AS_USER)
    other = client.put(url, json={"planted_on": "2026-11-02"}, headers=AS_USER)

    assert (future.status_code, future.json()["code"]) == (422, "validation-error")
    assert first.status_code == same.status_code == 200
    assert (other.status_code, other.json()["code"]) == (409, "planting-already-recorded")
    assert client.get(f"/v1/journals/{journal_id}", headers=AS_USER).json()["planted_on"] == (
        "2026-11-01"
    )


def test_an_update_is_idempotent_and_reserves_quota_once(
    client: TestClient, journal_id, quota: FakeAiQuota
):
    body = {
        "id": str(uuid.uuid4()),
        **QUESTIONNAIRE,
        "sensor_reading": "35.50",
        "sensor_unit": "vwc_pct",
        "air_measured_at": "2026-11-11T09:30:00+01:00",
    }
    url = f"/v1/journals/{journal_id}/updates"

    first = client.post(url, json=body, headers=AS_USER)
    again = client.post(url, json=body, headers=AS_USER)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert first.json()["sensor_reading"] == "35.50"
    assert first.json()["sensor_unit"] == "vwc_pct"
    assert first.json()["watering_method"] == "unknown"
    assert first.json()["air_measured_at"] == "2026-11-11T08:30:00Z"
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
        page = client.get(url, params={"cursor": cursor} if cursor else {}, headers=AS_USER).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == ids[::-1]


def test_a_photo_is_stored_only_once_confirmed_and_confirming_again_changes_nothing(
    client: TestClient, storage: FakePhotoStorage, journal_id, engine: Engine
):
    update_id = submit(client, journal_id).json()["id"]
    url = f"/v1/updates/{update_id}/photos"
    body = {"kind": "overview", "content_type": "image/png", "size_bytes": 1000}

    presigned = client.post(url, json=body, headers=AS_USER)
    with Session(engine) as session:
        stored_before = session.scalars(select(UpdatePhotoModel.storage_key)).all()
    listed_before = client.get(url, headers=AS_USER).json()
    upload_photo(client, storage, AS_USER, journal_id, update_id, "overview")
    again = upload_photo(client, storage, AS_USER, journal_id, update_id, "overview")
    listed = client.get(url, headers=AS_USER).json()
    with Session(engine) as session:
        stored = session.scalars(select(UpdatePhotoModel.storage_key)).all()

    assert presigned.status_code == 200
    assert stored_before == []
    assert listed_before == {"photos": []}
    assert again.status_code == 200
    assert [photo["kind"] for photo in listed["photos"]] == ["overview"]
    assert stored == [f"journals/{journal_id}/updates/{update_id}/overview"]


def test_a_rejected_object_is_deleted_and_not_stored(
    client: TestClient, storage: FakePhotoStorage, journal_id, engine: Engine
):
    update_id = submit(client, journal_id).json()["id"]
    key = f"journals/{journal_id}/updates/{update_id}/symptom_closeup"
    storage.put(key, size_bytes=6_000_000)

    response = client.post(
        f"/v1/updates/{update_id}/photos/symptom_closeup/confirm", headers=AS_USER
    )

    with Session(engine) as session:
        stored = session.scalars(select(UpdatePhotoModel.storage_key)).all()
    assert response.status_code == 422
    assert response.json()["code"] == "photo-too-large"
    assert storage.deleted == [key]
    assert stored == []


def test_another_user_sees_nothing(client: TestClient, journal_id):
    update_id = submit(client, journal_id).json()["id"]
    stranger = {"X-User-Id": str(uuid.uuid4()), "X-User-Groups": "users"}

    assert client.get(f"/v1/journals/{journal_id}", headers=stranger).status_code == 404
    assert client.get(f"/v1/journals/{journal_id}/updates", headers=stranger).status_code == 404
    assert client.get(f"/v1/updates/{update_id}/photos", headers=stranger).status_code == 404
    assert client.get("/v1/journals", headers=stranger).json() == {"items": [], "next_cursor": None}
