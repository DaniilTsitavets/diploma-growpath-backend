from pathlib import Path

import pytest

from app.service.knowledge_model import SyncResult
from app.service.knowledge_parser import InvalidKnowledgeFile
from app.service.knowledge_seed import KnowledgeSeeder
from tests.unit.fakes import FakeUnitOfWork
from tests.unit.knowledge_fakes import FakeKnowledge

HEADER = (
    "---\nslug: {slug}\ntitle: Title {slug}\nspecies:\nsource_title: Notes\n"
    "source_url: https://example.test/{slug}\n---\n\n"
)


class KnowledgeUnitOfWork(FakeUnitOfWork):
    def __init__(self) -> None:
        super().__init__()
        self.knowledge = FakeKnowledge()


def write_file(directory: Path, slug: str, body: str = "## Water\n\nWhen dry.\n") -> None:
    (directory / f"{slug}.md").write_text(HEADER.format(slug=slug) + body, encoding="utf-8")


def seeder_for(directory: Path) -> tuple[KnowledgeSeeder, KnowledgeUnitOfWork]:
    uow = KnowledgeUnitOfWork()
    return KnowledgeSeeder(lambda: uow, directory), uow


def test_every_file_is_loaded_in_its_own_committed_unit(tmp_path: Path):
    write_file(tmp_path, "basil")
    write_file(tmp_path, "mint")
    seeder, uow = seeder_for(tmp_path)

    results = seeder.run()

    assert results == {"basil": SyncResult.CREATED, "mint": SyncResult.CREATED}
    assert uow.commits == 2
    assert {document.slug for document in uow.knowledge.documents.values()} == {"basil", "mint"}


def test_running_again_changes_nothing(tmp_path: Path):
    write_file(tmp_path, "basil")
    seeder, uow = seeder_for(tmp_path)
    seeder.run()

    assert seeder.run() == {"basil": SyncResult.UNCHANGED}


def test_an_edited_file_updates_its_document(tmp_path: Path):
    write_file(tmp_path, "basil")
    seeder, uow = seeder_for(tmp_path)
    seeder.run()
    write_file(tmp_path, "basil", "## Water\n\nWhen the soil is dry.\n")

    assert seeder.run() == {"basil": SyncResult.UPDATED}


def test_an_invalid_file_stops_the_load_before_anything_is_written(tmp_path: Path):
    write_file(tmp_path, "basil")
    (tmp_path / "broken.md").write_text("no header", encoding="utf-8")
    seeder, uow = seeder_for(tmp_path)

    with pytest.raises(InvalidKnowledgeFile, match="broken.md"):
        seeder.run()

    assert uow.knowledge.documents == {} and uow.commits == 0


def test_a_file_that_is_not_utf8_is_reported_by_name(tmp_path: Path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe---")
    seeder, _ = seeder_for(tmp_path)

    with pytest.raises(InvalidKnowledgeFile, match="bad.md"):
        seeder.run()


def test_an_empty_directory_is_an_error(tmp_path: Path):
    seeder, _ = seeder_for(tmp_path)

    with pytest.raises(InvalidKnowledgeFile, match="no knowledge files"):
        seeder.run()


def test_two_files_with_one_slug_are_rejected(tmp_path: Path):
    write_file(tmp_path, "basil")
    (tmp_path / "copy.md").write_text(HEADER.format(slug="basil") + "## Water\n\nDry.\n")
    seeder, _ = seeder_for(tmp_path)

    with pytest.raises(InvalidKnowledgeFile, match="share a slug"):
        seeder.run()
