import dataclasses
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.knowledge_import.__main__ import DEFAULT_PLANTS_FILE, parse_args
from app.knowledge_import.run import ImportRunner, read_plant_slugs
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
    return ImportRunner(source, lambda: unit_of_work, plants, lambda: today, dry_run)


def test_a_first_run_creates_the_plant_and_the_problems_each_in_its_own_unit():
    unit_of_work = FakeKnowledgeUnitOfWork()

    report = runner(FakeSource(), unit_of_work).run()

    assert set(report.created) == DOCUMENTS
    assert report.updated == report.unchanged == report.stale == []
    assert report.succeeded
    assert {d.slug for d in unit_of_work.knowledge.documents.values()} == DOCUMENTS
    assert unit_of_work.commits == 3


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

    assert report.summary() == "created=3 updated=0 unchanged=0 failed=1 stale=0"


def test_the_plants_file_skips_comments_blanks_and_repeats(tmp_path: Path):
    path = tmp_path / "plants.txt"
    path.write_text("# header\nlavender\n\n  aloe-vera  # tidy\nlavender\n", encoding="utf-8")

    assert read_plant_slugs(path) == ["lavender", "aloe-vera"]


def test_a_bad_slug_in_the_plants_file_is_refused_with_its_line(tmp_path: Path):
    path = tmp_path / "plants.txt"
    path.write_text("lavender\n../etc\n", encoding="utf-8")

    with pytest.raises(ValueError, match=":2:"):
        read_plant_slugs(path)


def test_the_shipped_plants_file_lists_the_fifteen_agreed_slugs():
    assert read_plant_slugs(DEFAULT_PLANTS_FILE) == [
        "lavender",
        "holy-basil-tulsi-care",
        "golden-pothos-care",
        "snake-plant",
        "zz-plant",
        "jade-plant",
        "peace-lily",
        "aloe-vera",
        "spider-plant",
        "monstera-deliciosa",
        "ficus-lyrata",
        "boston-fern",
        "calathea-orbifolia",
        "cactus",
        "maranta-prayer-plant-care",
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
