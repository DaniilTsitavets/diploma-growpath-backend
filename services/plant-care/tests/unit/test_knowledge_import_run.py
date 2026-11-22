import dataclasses
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.knowledge_import.__main__ import DEFAULT_PLANTS_FILE, parse_args
from app.knowledge_import.plants import PlantEntry, read_plant_entries
from app.knowledge_import.run import ImportRunner
from app.knowledge_import.settings import ImportSettings
from app.knowledge_import.source import SourceError
from tests.unit.knowledge_fakes import FakeKnowledgeUnitOfWork
from tests.unit.plantsolve_data import FakeSource

DAY = date(2026, 11, 22)
DOCUMENTS = {
    "plantsolve-lavender",
    "plantsolve-problem-overwatering",
    "plantsolve-problem-root-rot",
}


def runner(
    source: FakeSource,
    unit_of_work: FakeKnowledgeUnitOfWork,
    plants: tuple[str, ...] = ("lavender",),
    today: date = DAY,
    dry_run: bool = False,
) -> ImportRunner:
    entries = [PlantEntry(slug, slug, slug.capitalize()) for slug in plants]
    return ImportRunner(source, lambda: unit_of_work, entries, lambda: today, dry_run)


def test_a_first_run_creates_the_plant_and_the_problems_each_in_its_own_unit():
    unit_of_work = FakeKnowledgeUnitOfWork()

    report = runner(FakeSource(), unit_of_work).run()

    assert set(report.created) == DOCUMENTS
    assert report.updated == report.unchanged == report.stale == []
    assert report.succeeded
    assert {d.slug for d in unit_of_work.knowledge.documents.values()} == DOCUMENTS
    assert unit_of_work.commits == 3
    assert report.profiles.created == ["lavender"]
    assert unit_of_work.plant_profiles.rows["lavender"].species == "Lavender"


def test_a_plant_is_stored_under_the_species_of_its_entry_with_its_profile():
    unit_of_work = FakeKnowledgeUnitOfWork()
    entry = PlantEntry("lavender", "english-lavender", "English Lavender")

    ImportRunner(FakeSource(), lambda: unit_of_work, [entry], lambda: DAY).run()

    document = next(
        d for d in unit_of_work.knowledge.documents.values() if d.slug == "plantsolve-lavender"
    )
    assert document.species == "English Lavender"
    assert unit_of_work.plant_profiles.rows["english-lavender"].source_slug == "lavender"


def test_a_profile_is_not_rewritten_by_an_unchanged_run_but_follows_changed_text():
    unit_of_work = FakeKnowledgeUnitOfWork()
    runner(FakeSource(), unit_of_work).run()
    again = runner(FakeSource(), unit_of_work, today=DAY + timedelta(days=5)).run()
    source = FakeSource()
    source.plants["lavender"] = {**source.plants["lavender"], "summary": "A rewritten summary."}

    changed = runner(source, unit_of_work, today=DAY + timedelta(days=6)).run()

    assert again.profiles.unchanged == ["lavender"]
    assert changed.profiles.updated == ["lavender"]
    assert unit_of_work.plant_profiles.rows["lavender"].imported_on == DAY + timedelta(days=6)


def test_a_dry_run_compares_profiles_without_writing():
    unit_of_work = FakeKnowledgeUnitOfWork()
    first = runner(FakeSource(), unit_of_work, dry_run=True).run()
    runner(FakeSource(), unit_of_work).run()
    source = FakeSource()
    source.plants["lavender"] = {**source.plants["lavender"], "summary": "Different."}

    second = runner(source, unit_of_work, dry_run=True).run()

    assert first.profiles.created == ["lavender"]
    assert second.profiles.updated == ["lavender"]
    assert unit_of_work.plant_profiles.rows["lavender"].summary != "Different."


def test_a_second_run_on_unchanged_data_changes_nothing_even_on_another_day():
    unit_of_work = FakeKnowledgeUnitOfWork()
    runner(FakeSource(), unit_of_work).run()

    report = runner(FakeSource(), unit_of_work, today=DAY + timedelta(days=30)).run()

    assert set(report.unchanged) == DOCUMENTS
    assert report.created == report.updated == []
    assert {d.imported_on for d in unit_of_work.knowledge.documents.values()} == {DAY}


def test_changed_source_text_updates_only_that_document():
    unit_of_work = FakeKnowledgeUnitOfWork()
    runner(FakeSource(), unit_of_work).run()
    source = FakeSource()
    source.plants["lavender"] = {**source.plants["lavender"], "summary": "A rewritten summary."}

    report = runner(source, unit_of_work, today=DAY + timedelta(days=1)).run()

    assert report.updated == ["plantsolve-lavender"]
    assert len(report.unchanged) == 2
    stored = next(
        d for d in unit_of_work.knowledge.documents.values() if d.slug == "plantsolve-lavender"
    )
    assert stored.imported_on == DAY + timedelta(days=1)


def test_one_missing_plant_is_reported_and_the_others_still_go_in():
    unit_of_work = FakeKnowledgeUnitOfWork()

    report = runner(FakeSource(), unit_of_work, plants=("ghost", "lavender")).run()

    assert not report.succeeded
    assert list(report.failed) == ["plantsolve-ghost"]
    assert set(report.created) == DOCUMENTS


def test_a_plant_with_a_bad_licence_or_a_broken_fetch_fails_alone():
    source = FakeSource()
    source.plants["bad-licence"] = {
        **source.plants["lavender"],
        "slug": "bad-licence",
        "citation": {
            "canonicalUrl": "https://www.plantsolve.com/plants/x/",
            "license": "Proprietary",
        },
    }
    source.broken.add("flaky")
    unit_of_work = FakeKnowledgeUnitOfWork()

    report = runner(source, unit_of_work, plants=("bad-licence", "flaky", "lavender")).run()

    assert set(report.failed) == {"plantsolve-bad-licence", "plantsolve-flaky"}
    assert "CC BY" in report.failed["plantsolve-bad-licence"]
    assert "plantsolve-lavender" in report.created


def test_a_failed_problem_does_not_stop_the_rest():
    source = FakeSource()
    del source.problems["root-rot"]

    report = runner(source, FakeKnowledgeUnitOfWork()).run()

    assert list(report.failed) == ["plantsolve-problem-root-rot"]
    assert set(report.created) == DOCUMENTS - {"plantsolve-problem-root-rot"}


def test_a_database_error_on_one_document_is_reported_and_the_others_still_go_in():
    unit_of_work = FakeKnowledgeUnitOfWork()
    unit_of_work.sync_failures.add("plantsolve-lavender")

    report = runner(FakeSource(), unit_of_work).run()

    assert report.failed == {"plantsolve-lavender": "database error: SQLAlchemyError"}
    assert set(report.created) == DOCUMENTS - {"plantsolve-lavender"}
    assert report.profiles.created == []


def test_a_profile_write_error_fails_the_plant_and_its_document_is_not_counted():
    unit_of_work = FakeKnowledgeUnitOfWork()

    def broken(profile):
        raise SQLAlchemyError("write failed")

    unit_of_work.plant_profiles.sync_profile = broken

    report = runner(FakeSource(), unit_of_work).run()

    assert report.failed == {"plantsolve-lavender": "database error: SQLAlchemyError"}
    assert "plantsolve-lavender" not in report.created
    assert report.profiles.created == []


def test_the_shared_lists_are_required():
    class NoRelationships(FakeSource):
        def relationships(self):
            raise SourceError("relationships.json: HTTP 500")

    with pytest.raises(SourceError):
        runner(NoRelationships(), FakeKnowledgeUnitOfWork()).run()


def test_stored_plantsolve_documents_that_are_not_in_the_run_are_reported_not_deleted():
    unit_of_work = FakeKnowledgeUnitOfWork()
    runner(FakeSource(), unit_of_work).run()
    old = next(iter(unit_of_work.knowledge.documents.values()))
    unit_of_work.knowledge.documents[uuid.uuid4()] = dataclasses.replace(
        old, id=uuid.uuid4(), slug="plantsolve-retired"
    )
    unit_of_work.knowledge.documents[uuid.uuid4()] = dataclasses.replace(
        old, id=uuid.uuid4(), slug="handwritten-basil"
    )

    report = runner(FakeSource(), unit_of_work).run()

    assert report.stale == ["plantsolve-retired"]
    assert "plantsolve-retired" in {d.slug for d in unit_of_work.knowledge.documents.values()}


def test_a_dry_run_writes_nothing_and_tells_what_would_happen():
    unit_of_work = FakeKnowledgeUnitOfWork()

    report = runner(FakeSource(), unit_of_work, dry_run=True).run()

    assert set(report.created) == DOCUMENTS
    assert unit_of_work.knowledge.documents == {}
    assert unit_of_work.commits == 0
    assert "dry run" in report.summary()


def test_a_dry_run_compares_with_the_stored_hashes():
    unit_of_work = FakeKnowledgeUnitOfWork()
    runner(FakeSource(), unit_of_work).run()
    source = FakeSource()
    source.plants["lavender"] = {**source.plants["lavender"], "summary": "Different."}
    commits = unit_of_work.commits

    report = runner(source, unit_of_work, dry_run=True).run()

    assert report.updated == ["plantsolve-lavender"]
    assert len(report.unchanged) == 2
    assert unit_of_work.commits == commits


def test_the_summary_counts_every_outcome():
    report = runner(FakeSource(), FakeKnowledgeUnitOfWork(), plants=("ghost", "lavender")).run()

    assert report.summary() == (
        "created=3 updated=0 unchanged=0 failed=1 stale=0 profiles: created=1 updated=0 unchanged=0"
    )


def test_the_dry_run_summary_ends_with_the_dry_run_note():
    report = runner(FakeSource(), FakeKnowledgeUnitOfWork(), dry_run=True).run()

    assert report.summary().endswith(
        "profiles: created=1 updated=0 unchanged=0 (dry run, nothing written)"
    )


def plants_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "plants.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_plants_file_skips_comments_and_blanks_and_trims_fields(tmp_path: Path):
    path = plants_file(
        tmp_path,
        "# header\nlavender | lavender | Lavender\n\n  aloe-vera|aloe|  Aloe Vera  # tidy\n",
    )

    assert read_plant_entries(path) == [
        PlantEntry("lavender", "lavender", "Lavender"),
        PlantEntry("aloe-vera", "aloe", "Aloe Vera"),
    ]


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("lavender\n", ":1:"),
        ("ok | ok | Ok\n../etc | etc | Etc\n", ":2:"),
        ("ok | Bad Slug | Ok\n", ":1:"),
        ("ok | " + "a" * 65 + " | Ok\n", ":1:"),
        ("ok | ok | \n", ":1:"),
        ("ok | ok | " + "S" * 81 + "\n", ":1:"),
        ("a | a | A\na | b | B\n", ":2:"),
        ("a | a | A\nb | a | B\n", ":2:"),
        ("a | b | c | d\n", ":1:"),
    ],
)
def test_a_bad_line_in_the_plants_file_is_refused_with_its_number(
    tmp_path: Path, text: str, line: str
):
    with pytest.raises(ValueError, match=line):
        read_plant_entries(plants_file(tmp_path, text))


def test_the_shipped_plants_file_lists_the_fifteen_agreed_plants():
    entries = read_plant_entries(DEFAULT_PLANTS_FILE)

    assert [(e.source_slug, e.slug, e.species) for e in entries] == [
        ("lavender", "lavender", "Lavender"),
        ("holy-basil-tulsi-care", "holy-basil-tulsi", "Holy Basil (Tulsi)"),
        ("golden-pothos-care", "golden-pothos", "Golden Pothos"),
        ("snake-plant", "snake-plant", "Snake Plant"),
        ("zz-plant", "zz-plant", "ZZ Plant"),
        ("jade-plant", "jade-plant", "Jade Plant"),
        ("peace-lily", "peace-lily", "Peace Lily"),
        ("aloe-vera", "aloe-vera", "Aloe Vera"),
        ("spider-plant", "spider-plant", "Spider Plant"),
        ("monstera-deliciosa", "monstera-deliciosa", "Monstera Deliciosa"),
        ("ficus-lyrata", "ficus-lyrata", "Fiddle Leaf Fig"),
        ("boston-fern", "boston-fern", "Boston Fern"),
        ("calathea-orbifolia", "calathea-orbifolia", "Calathea Orbifolia"),
        ("cactus", "cactus", "Cactus"),
        ("maranta-prayer-plant-care", "maranta-prayer-plant", "Maranta Prayer Plant"),
    ]


def test_the_command_line_has_the_documented_options():
    args = parse_args(["--dry-run", "--plants-file", "x.txt", "--pause", "0.5"])

    assert (args.dry_run, args.plants_file, args.pause) == (True, Path("x.txt"), 0.5)
    assert parse_args([]).pause is None


def test_the_settings_load_with_only_the_database_url(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANT_CARE_LOG_LEVEL", "PLANT_CARE_ENTITLEMENT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLANT_CARE_DATABASE_URL", "postgresql+psycopg://u:secret@db/plant")

    settings = ImportSettings(_env_file=None)

    assert settings.log_level == "INFO"
    assert "secret" not in repr(settings)
    assert settings.fetch_policy().pause_seconds == 2.0
    assert settings.fetch_policy(0.0).pause_seconds == 0.0
    assert settings.fetch_policy().max_response_bytes == 2_000_000


def test_the_settings_have_no_default_for_the_database_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PLANT_CARE_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        ImportSettings(_env_file=None)
