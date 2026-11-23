import dataclasses
from datetime import date

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import PlantProfileModel
from app.repository.plant_profiles import SqlAlchemyPlantProfileRepository
from app.service.errors import Unavailable
from app.service.knowledge_model import SyncResult
from app.service.plant_profile_model import PlantProfile
from tests.db.factories import violated

DAY = date(2026, 11, 22)


def profile(slug: str = "lavender", sha: str = "a", **overrides) -> PlantProfile:
    fields = {
        "slug": slug,
        "source_slug": f"{slug}-care",
        "species": "Lavender",
        "source_name": "PlantSolve",
        "source_url": "https://www.plantsolve.com/plants/lavender/",
        "license": "CC BY 4.0",
        "imported_on": DAY,
        "content_sha256": sha * 64,
    }
    return PlantProfile(**{**fields, **overrides})


def add_row(session: Session, **overrides) -> dict:
    row = {**dataclasses.asdict(profile()), **overrides}
    session.execute(insert(PlantProfileModel).values(**row))
    return row


def rejected_by(session: Session, constraint: str, **fields) -> None:
    with violated(session) as violation:
        add_row(session, **fields)

    assert violation.constraint == constraint


@pytest.mark.parametrize("slug", ["", "Lavender", "lav ender", "lav_ender", "x" * 65])
def test_a_slug_is_lowercase_ascii_with_dashes(session: Session, slug: str):
    rejected_by(session, "ck_plant_profiles_slug", slug=slug)


def test_the_slug_is_the_primary_key(session: Session):
    add_row(session)

    rejected_by(session, "pk_plant_profiles", source_slug="other")


def test_the_source_slug_is_unique(session: Session):
    add_row(session)

    rejected_by(session, "uq_plant_profiles_source_slug", slug="other")


@pytest.mark.parametrize(
    "fields",
    [
        {"species": " "},
        {"species": "s" * 81},
        {"source_slug": " "},
        {"source_name": ""},
        {"source_name": "n" * 101},
    ],
)
def test_names_are_non_blank_and_bounded(session: Session, fields: dict):
    rejected_by(session, "ck_plant_profiles_lengths", **fields)


@pytest.mark.parametrize("column", ["summary", "toxicity", "common_name", "humidity"])
def test_an_optional_text_has_at_most_2000_characters(session: Session, column: str):
    add_row(session, **{column: "t" * 2000})

    rejected_by(
        session,
        "ck_plant_profiles_text_lengths",
        slug="more",
        source_slug="more-care",
        **{column: "t" * 2001},
    )


def test_the_optional_texts_may_all_be_missing(session: Session):
    row = add_row(session, slug="bare", source_slug="bare-care", key_takeaways=None)

    stored = SqlAlchemyPlantProfileRepository(session).get(row["slug"])

    assert stored is not None and stored.summary is None and stored.key_takeaways is None


def test_key_takeaways_are_an_array_of_at_most_twelve(session: Session):
    add_row(session, slug="twelve", source_slug="twelve-care", key_takeaways=["x"] * 12)

    rejected_by(session, "ck_plant_profiles_key_takeaways", key_takeaways=["x"] * 13)
    rejected_by(session, "ck_plant_profiles_key_takeaways", key_takeaways={"a": 1})


@pytest.mark.parametrize("url", ["http://www.plantsolve.com/x", "www.plantsolve.com", ""])
def test_the_source_must_be_an_https_page(session: Session, url: str):
    rejected_by(session, "ck_plant_profiles_source_url", source_url=url)


def test_the_source_url_is_bounded(session: Session):
    rejected_by(session, "ck_plant_profiles_source_url", source_url="https://x.test/" + "a" * 990)


@pytest.mark.parametrize("license_text", ["", "  ", "l" * 101])
def test_the_licence_is_non_blank_and_bounded(session: Session, license_text: str):
    rejected_by(session, "ck_plant_profiles_license", license=license_text)


@pytest.mark.parametrize("sha", ["", "abc", "G" * 64, "A" * 64, "0" * 63])
def test_the_hash_is_64_lowercase_hex_digits(session: Session, sha: str):
    rejected_by(session, "ck_plant_profiles_sha", content_sha256=sha)


@pytest.mark.parametrize(
    "column", ["species", "source_name", "source_url", "license", "imported_on", "content_sha256"]
)
def test_the_species_source_licence_date_and_hash_are_required(session: Session, column: str):
    with pytest.raises(IntegrityError, match="null value"), session.begin_nested():
        add_row(session, **{column: None})


def test_the_creation_time_defaults_to_now(session: Session):
    add_row(session)

    created = session.execute(text("SELECT created_at = now() FROM plant_profiles")).scalar_one()

    assert created is True


def test_sync_creates_a_missing_profile_and_reads_it_back(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    new = profile(summary="A hardy herb.", key_takeaways=("Sun", "Grit"))

    assert repo.sync_profile(new) is SyncResult.CREATED

    assert repo.get("lavender") == new


def test_sync_with_the_same_hash_changes_nothing_even_on_another_day(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    repo.sync_profile(profile())
    later = profile(imported_on=date(2027, 1, 1), summary="ignored because the hash is equal")

    assert repo.sync_profile(later) is SyncResult.UNCHANGED

    stored = repo.get("lavender")
    assert stored.imported_on == DAY and stored.summary is None


def test_sync_with_a_new_hash_replaces_the_facts_and_the_import_date(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    repo.sync_profile(profile(summary="Old.", watering="Weekly."))
    changed = profile(sha="b", imported_on=date(2027, 1, 1), summary="New.")

    assert repo.sync_profile(changed) is SyncResult.UPDATED

    stored = repo.get("lavender")
    assert stored == changed and stored.watering is None


def test_sync_of_another_slug_with_a_taken_source_slug_is_refused(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    repo.sync_profile(profile("lavender"))

    with violated(session) as violation:
        repo.sync_profile(profile("english-lavender", source_slug="lavender-care"))

    assert violation.constraint == "uq_plant_profiles_source_slug"


def test_content_hashes_returns_the_stored_hash_of_each_known_slug(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    repo.sync_profile(profile("lavender", "a"))
    repo.sync_profile(profile("aloe-vera", "b"))

    assert repo.content_hashes(["lavender", "aloe-vera", "unknown"]) == {
        "lavender": "a" * 64,
        "aloe-vera": "b" * 64,
    }
    assert repo.content_hashes([]) == {}


def test_an_unknown_profile_is_none(session: Session):
    assert SqlAlchemyPlantProfileRepository(session).get("ghost") is None


WATERING_FACTS = {
    "dryness_threshold_pct": 100,
    "drought_tolerance": "high",
    "dormancy_shift": True,
    "soil_drainage": "fast",
    "temp_min_c": 18.3,
    "temp_max_c": 29.4,
    "rh_min_pct": 20,
    "rh_max_pct": None,
    "light_need": "direct_sun",
}


def test_the_watering_facts_round_trip_and_a_changed_one_updates_the_row(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    new = profile(**WATERING_FACTS)

    assert repo.sync_profile(new) is SyncResult.CREATED
    assert repo.get("lavender") == new

    changed = profile(sha="b", **{**WATERING_FACTS, "dryness_threshold_pct": 50})
    assert repo.sync_profile(changed) is SyncResult.UPDATED
    assert repo.get("lavender").dryness_threshold_pct == 50


@pytest.mark.parametrize(
    ("constraint", "fields"),
    [
        ("ck_plant_profiles_dryness_threshold", {"dryness_threshold_pct": 30}),
        ("ck_plant_profiles_drought_tolerance", {"drought_tolerance": "extreme"}),
        ("ck_plant_profiles_soil_drainage", {"soil_drainage": "slow"}),
        ("ck_plant_profiles_light_need", {"light_need": "bright"}),
        ("ck_plant_profiles_temp_range", {"temp_min_c": 25.0, "temp_max_c": 18.0}),
        ("ck_plant_profiles_temp_range", {"temp_min_c": -60.0}),
        ("ck_plant_profiles_temp_range", {"temp_max_c": 61.0}),
        ("ck_plant_profiles_rh_range", {"rh_min_pct": 70, "rh_max_pct": 40}),
        ("ck_plant_profiles_rh_range", {"rh_max_pct": 101}),
    ],
)
def test_the_watering_facts_are_bounded(session: Session, constraint: str, fields: dict):
    rejected_by(session, constraint, **fields)


def test_a_profile_is_found_by_its_species_ignoring_case(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    repo.sync_profile(profile("lavender", species="Lavender"))
    repo.sync_profile(profile("basil", species="Sweet Basil"))

    found = repo.get_by_species("sWEET basil")

    assert found is not None and found.slug == "basil"


def test_an_unknown_species_has_no_profile(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    repo.sync_profile(profile())

    assert repo.get_by_species("Mint") is None


def test_the_lowest_slug_wins_when_two_profiles_share_a_species(session: Session):
    repo = SqlAlchemyPlantProfileRepository(session)
    repo.sync_profile(profile("english-lavender", species="Lavender"))
    repo.sync_profile(profile("a-lavender", species="Lavender"))

    found = repo.get_by_species("Lavender")

    assert found is not None and found.slug == "a-lavender"


def test_the_species_lookup_reports_an_unreachable_database_as_unavailable(session: Session):
    class Failing:
        def execute(self, *args, **kwargs):
            raise OperationalError("select", {}, Exception("connection lost"))

    with pytest.raises(Unavailable):
        SqlAlchemyPlantProfileRepository(Failing()).get_by_species("Lavender")
