import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import ChatTurnModel, KbDocumentModel, KbSectionModel
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.diagnosis_model import ModelUnavailable
from app.service.knowledge_seed import KnowledgeSeeder
from app.service.model import Tier
from tests.db.factories import add_journal
from tests.unit.chat_fakes import FakeChatModel, answered
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import FakeAiQuota
from tests.unit.scenario import StaticTiers
from tests.unit.settings import make_settings

USER = uuid.uuid4()
AS_USER = {"X-User-Id": str(USER)}


@pytest.fixture
def quota() -> FakeAiQuota:
    return FakeAiQuota()


@pytest.fixture
def journal_id(engine: Engine) -> uuid.UUID:
    with Session(engine) as session:
        values = add_journal(session, user_id=USER, species="Basil")
        session.commit()
    return values["id"]


def app_for(database_url: URL, quota: FakeAiQuota, model: FakeChatModel, **overrides):
    settings = make_settings(database_url=database_url.render_as_string(hide_password=False))
    return create_app(
        settings,
        FakeQueue(),
        quota,
        FakePhotoStorage(),
        tier_lookup=StaticTiers(Tier.HOBBYIST),
        chat_model=model,
        **overrides,
    )


@pytest.fixture
def seeded(unit_of_work: Callable[[], SqlAlchemyUnitOfWork]) -> None:
    KnowledgeSeeder(unit_of_work, make_settings().knowledge_dir).run()


def test_a_question_goes_through_the_database_and_the_history_returns_it(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID, seeded: None, engine: Engine
):
    client = TestClient(app_for(database_url, quota, FakeChatModel(answered("S1"))))
    url = f"/v1/journals/{journal_id}/chat/turns"
    request = {"id": str(uuid.uuid4()), "question": "How do I pinch the tips of my basil?"}

    created = client.post(url, json=request, headers=AS_USER)
    replay = client.post(url, json=request, headers=AS_USER)
    history = client.get(url, headers=AS_USER)

    assert (created.status_code, replay.status_code) == (201, 200)
    assert replay.json() == created.json()
    (source,) = created.json()["sources"]
    assert source["rank"] == 1 and source["source_url"].startswith("https://")
    assert history.json()["items"] == [created.json()]
    assert len(quota.committed) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ChatTurnModel)) == 1


def test_a_model_failure_returns_the_found_sections_and_stores_nothing(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID, seeded: None, engine: Engine
):
    client = TestClient(app_for(database_url, quota, FakeChatModel(ModelUnavailable("down"))))

    response = client.post(
        f"/v1/journals/{journal_id}/chat/turns",
        json={"id": str(uuid.uuid4()), "question": "How often should I feed basil?"},
        headers=AS_USER,
    )

    body = response.json()
    assert response.status_code == 200 and body["outcome"] == "sources_only"
    assert len(body["related"]) == 4 and body["answer"] is None
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ChatTurnModel)) == 0
    assert quota.committed == [] and quota.released == quota.reserved


def test_a_journal_of_another_user_is_404(
    database_url: URL, quota: FakeAiQuota, journal_id: uuid.UUID, seeded: None
):
    client = TestClient(app_for(database_url, quota, FakeChatModel()))
    stranger = {"X-User-Id": str(uuid.uuid4())}

    response = client.get(f"/v1/journals/{journal_id}/chat/turns", headers=stranger)

    assert response.status_code == 404


def test_starting_the_application_loads_the_knowledge_base(
    database_url: URL, quota: FakeAiQuota, engine: Engine
):
    app = app_for(database_url, quota, FakeChatModel())

    with TestClient(app):
        pass

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(KbDocumentModel)) == 5
        assert session.scalar(select(func.count()).select_from(KbSectionModel)) >= 40
