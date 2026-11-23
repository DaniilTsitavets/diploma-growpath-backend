from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.catalog_labels import load_label_table
from app.main import create_app
from app.models import CatalogItem, CatalogItemLabel
from app.seed import CATALOG, seed_catalog
from tests.unit.fakes import RecordingEvents, make_settings

DERIVED = load_label_table()
PACKS = {item.slug: item for item in CATALOG if item.item_type == "pack"}


@pytest.fixture
def client(database_url: URL) -> TestClient:
    settings = make_settings(database_url=database_url.render_as_string(hide_password=False))
    return TestClient(create_app(settings, RecordingEvents()), raise_server_exceptions=False)


@pytest.fixture
def seeded_catalog(engine: Engine) -> Iterator[None]:
    with Session(engine) as session, session.begin():
        seed_catalog(session)
    yield
    with Session(engine) as session, session.begin():
        ids = [item.id for item in CATALOG]
        session.execute(delete(CatalogItemLabel).where(CatalogItemLabel.item_id.in_(ids)))
        session.execute(delete(CatalogItem).where(CatalogItem.id.in_(ids)))


def expected(slug: str) -> list[str]:
    return sorted(PACKS[slug].wanted_labels(DERIVED))


@pytest.mark.usefixtures("seeded_catalog")
def test_the_list_returns_the_seeded_labels_of_every_pack(client: TestClient):
    items = client.get("/v1/items", params={"limit": 100}).json()["items"]

    labels = {item["slug"]: item["labels"] for item in items}
    assert {slug: labels[slug] for slug in PACKS} == {slug: expected(slug) for slug in PACKS}
    assert "toxic-to-pets" in labels["aloe-vera"] and "rare" in labels["ficus-lyrata"]


@pytest.mark.usefixtures("seeded_catalog")
def test_the_detail_returns_the_seeded_labels(client: TestClient):
    body = client.get("/v1/items/cactus").json()

    assert body["labels"] == expected("cactus")
    assert {"beginner", "drought-tolerant", "direct-sun"} <= set(body["labels"])


@pytest.mark.usefixtures("seeded_catalog")
@pytest.mark.parametrize("label", ["pet-safe", "low-light", "drought-tolerant", "rare", "beginner"])
def test_the_label_filter_finds_exactly_the_packs_that_carry_the_label(
    client: TestClient, label: str
):
    body = client.get("/v1/items", params={"label": label, "limit": 100}).json()

    assert [item["slug"] for item in body["items"]] == sorted(
        slug for slug in PACKS if label in expected(slug)
    )
    assert all(label in item["labels"] for item in body["items"])
