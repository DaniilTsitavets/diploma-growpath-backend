import uuid
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class SyncResult(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class SectionDraft:
    id: uuid.UUID
    position: int
    anchor: str
    heading: str
    body: str


@dataclass(frozen=True)
class DocumentDraft:
    """A knowledge document as the importer built it; `species` None means general care."""

    id: uuid.UUID
    slug: str
    title: str
    species: str | None
    source_title: str
    source_url: str
    content_sha256: str
    sections: list[SectionDraft]
    license: str | None = None
    imported_on: date | None = None


@dataclass(frozen=True)
class RetrievedSection:
    id: uuid.UUID
    document_title: str
    source_url: str
    source_name: str
    license: str | None
    heading: str
    body: str
