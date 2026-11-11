import logging
from collections.abc import Callable
from pathlib import Path

from app.service.knowledge_model import DocumentDraft, SyncResult
from app.service.knowledge_parser import InvalidKnowledgeFile, parse_document
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


class KnowledgeSeeder:
    """Loads the Markdown files of a directory into the knowledge base; repeating it is harmless."""

    def __init__(self, unit_of_work: Callable[[], UnitOfWork], directory: Path) -> None:
        self._unit_of_work = unit_of_work
        self._directory = directory

    def run(self) -> dict[str, SyncResult]:
        """Every file is parsed before the first write, so an invalid file changes nothing."""
        documents = self._read_documents()
        results: dict[str, SyncResult] = {}
        for document in documents:
            with self._unit_of_work() as uow:
                results[document.slug] = uow.knowledge.sync(document)
                uow.commit()
            logger.info(
                "knowledge document synced",
                extra={"slug": document.slug, "sync_result": results[document.slug].value},
            )
        return results

    def _read_documents(self) -> list[DocumentDraft]:
        paths = sorted(self._directory.glob("*.md"))
        if not paths:
            raise InvalidKnowledgeFile(f"no knowledge files in {self._directory}")
        documents: list[DocumentDraft] = []
        for path in paths:
            try:
                documents.append(parse_document(path.read_text(encoding="utf-8")))
            except (InvalidKnowledgeFile, UnicodeDecodeError) as error:
                raise InvalidKnowledgeFile(f"{path.name}: {error}") from error
        slugs = [document.slug for document in documents]
        if len(set(slugs)) != len(slugs):
            raise InvalidKnowledgeFile("two knowledge files share a slug")
        return documents
