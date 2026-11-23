import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CorrelationIdMiddleware
from sqlalchemy.exc import OperationalError

from app.api import plants
from app.api.errors import register_error_handlers
from app.repository.plant_profiles import SqlAlchemyPlantProfileRepository
from app.service.errors import Unavailable
from app.service.plant_profile_model import FaqEntry
from app.service.plants import PlantService
from tests.unit.fakes import FakeUnitOfWork

PROBLEM_JSON = "application/problem+json"


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def client(uow: FakeUnitOfWork) -> TestClient:
    app = FastAPI()
    app.state.plant_service = PlantService(lambda: uow)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app, retry_after_seconds=7)
    app.include_router(plants.router)
    return TestClient(app, raise_server_exceptions=False)


def test_a_full_profile_is_served_without_signing_in(client: TestClient, uow: FakeUnitOfWork):
    uow.plant_profiles.seed(
        "lavender",
        common_name="Lavender",
        scientific_name="Lavandula angustifolia",
        summary="A hardy herb.",
        key_takeaways=("Sun", "Grit"),
        watering="Rarely.",
    )

    response = client.get("/v1/plants/lavender")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    body = response.json()
    assert body["slug"] == "lavender" and body["species"] == "Lavender"
    assert body["updated_at"] == "2026-11-22"
    assert body["sources"] == [
        {
            "name": "PlantSolve",
            "url": "https://www.plantsolve.com/plants/lavender/",
            "license": "CC BY 4.0",
        }
    ]
    assert body["key_takeaways"] == ["Sun", "Grit"]
    assert body["scientific_name"] == "Lavandula angustifolia"


def test_absent_facts_are_left_out_of_the_json(client: TestClient, uow: FakeUnitOfWork):
    uow.plant_profiles.seed("cactus", species="Cactus")

    body = client.get("/v1/plants/cactus").json()

    assert set(body) == {"slug", "species", "sources", "updated_at"}


def test_an_unknown_plant_is_a_404_problem(client: TestClient):
    response = client.get("/v1/plants/ghost")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["title"] == "Plant not found"
    assert "cache-control" not in response.headers


@pytest.mark.parametrize("slug", ["Lavender", "bad_slug", "x" * 65, "lav.ender"])
def test_a_slug_that_does_not_match_the_pattern_is_a_404_without_a_lookup(
    client: TestClient, uow: FakeUnitOfWork, slug: str
):
    uow.plant_profiles.get_error = AssertionError("the database must not be asked")

    response = client.get(f"/v1/plants/{slug}")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON


def test_an_unavailable_database_is_a_503_with_retry_after(client: TestClient, uow: FakeUnitOfWork):
    uow.plant_profiles.get_error = Unavailable("the database did not answer")

    response = client.get("/v1/plants/lavender")

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["retry-after"] == "7"
    assert "cache-control" not in response.headers


class BrokenSession:
    def execute(self, statement):
        raise OperationalError("SELECT", {}, ConnectionError("refused"))


def test_the_repository_turns_a_connection_failure_into_unavailable():
    repository = SqlAlchemyPlantProfileRepository(BrokenSession())

    with pytest.raises(Unavailable):
        repository.get("lavender")


def test_the_service_serves_only_profiles_of_the_pattern(uow: FakeUnitOfWork):
    uow.plant_profiles.seed("lavender")

    assert PlantService(lambda: uow).get("lavender").slug == "lavender"


def test_the_plant_router_does_not_read_the_user_header(client: TestClient, uow: FakeUnitOfWork):
    uow.plant_profiles.seed("lavender")

    response = client.get("/v1/plants/lavender", headers={"X-User-Id": "not-a-uuid"})

    assert response.status_code == 200


def test_app_wires_the_plant_service():
    from app.main import create_app
    from tests.unit.fake_queue import FakeQueue
    from tests.unit.fake_storage import FakePhotoStorage
    from tests.unit.settings import make_settings

    app = create_app(
        make_settings(), FakeQueue(), photo_storage=FakePhotoStorage(), ticket_queue=FakeQueue()
    )

    assert isinstance(app.state.plant_service, PlantService)


def test_the_faq_and_the_labels_are_served_when_the_profile_has_them(
    client: TestClient, uow: FakeUnitOfWork
):
    uow.plant_profiles.seed(
        "aloe-vera",
        faq=(FaqEntry("Q1?", "A1."), FaqEntry("Q2?", "A2.")),
        labels=("beginner", "pet-safe"),
    )

    body = client.get("/v1/plants/aloe-vera").json()

    assert body["faq"] == [
        {"question": "Q1?", "answer": "A1."},
        {"question": "Q2?", "answer": "A2."},
    ]
    assert body["labels"] == ["beginner", "pet-safe"]


def test_an_absent_faq_and_empty_labels_are_left_out(client: TestClient, uow: FakeUnitOfWork):
    uow.plant_profiles.seed("cactus", faq=None, labels=())

    body = client.get("/v1/plants/cactus").json()

    assert "faq" not in body and "labels" not in body
