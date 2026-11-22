import uuid
from collections.abc import Collection
from typing import Self

from sqlalchemy.exc import SQLAlchemyError

from app.service.knowledge_model import DocumentDraft, RetrievedSection, SyncResult


class FakeKnowledge:
    """Keeps documents by id and decides like the SQL repository: only a new hash rewrites."""

    def __init__(self) -> None:
        self.documents: dict[uuid.UUID, DocumentDraft] = {}
        self.found: list[RetrievedSection] = []
        self.searches: list[tuple[str, str, int]] = []
        self.search_error: Exception | None = None

    def sync(self, document: DocumentDraft) -> SyncResult:
        stored = self.documents.get(document.id)
        if stored is None:
            result = SyncResult.CREATED
        elif stored.content_sha256 == document.content_sha256:
            return SyncResult.UNCHANGED
        else:
            result = SyncResult.UPDATED
        self.documents[document.id] = document
        return result

    def slugs_with_prefix(self, prefix: str) -> list[str]:
        return sorted(d.slug for d in self.documents.values() if d.slug.startswith(prefix))

    def content_hashes(self, slugs: Collection[str]) -> dict[str, str]:
        return {d.slug: d.content_sha256 for d in self.documents.values() if d.slug in slugs}

    def search(self, question: str, species: str, limit: int) -> list[RetrievedSection]:
        self.searches.append((question, species, limit))
        if self.search_error is not None:
            raise self.search_error
        return self.found[:limit]


class FakeKnowledgeUnitOfWork:
    """Every unit shares one FakeKnowledge; `sync_failures` lists slugs whose write fails."""

    def __init__(self) -> None:
        self.knowledge = FakeKnowledge()
        self.commits = 0
        self.units = 0
        self.sync_failures: set[str] = set()
        real_sync = self.knowledge.sync

        def sync(document: DocumentDraft) -> SyncResult:
            if document.slug in self.sync_failures:
                raise SQLAlchemyError("write failed")
            return real_sync(document)

        self.knowledge.sync = sync

    def __enter__(self) -> Self:
        self.units += 1
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


def make_section(number: int = 1, **overrides) -> RetrievedSection:
    fields = {
        "id": uuid.uuid5(uuid.NAMESPACE_URL, f"section-{number}"),
        "document_title": f"Document {number}",
        "source_url": f"https://example.test/{number}",
        "heading": f"Heading {number}",
        "body": f"Body of section {number}.",
    }
    return RetrievedSection(**{**fields, **overrides})
