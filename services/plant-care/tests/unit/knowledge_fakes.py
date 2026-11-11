import uuid

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

    def search(self, question: str, species: str, limit: int) -> list[RetrievedSection]:
        self.searches.append((question, species, limit))
        if self.search_error is not None:
            raise self.search_error
        return self.found[:limit]


def make_section(number: int = 1, **overrides) -> RetrievedSection:
    fields = {
        "id": uuid.uuid5(uuid.NAMESPACE_URL, f"section-{number}"),
        "document_title": f"Document {number}",
        "source_url": f"https://example.test/{number}",
        "heading": f"Heading {number}",
        "body": f"Body of section {number}.",
    }
    return RetrievedSection(**{**fields, **overrides})
