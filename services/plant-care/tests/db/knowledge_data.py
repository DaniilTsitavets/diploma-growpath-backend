import hashlib
import uuid
from collections.abc import Callable
from datetime import date

from app.knowledge_import.convert import anchor_of
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.knowledge_model import DocumentDraft, SectionDraft


def make_document(
    slug: str = "test-doc",
    species: str | None = "Basil",
    sections: dict[str, str] | None = None,
    title: str | None = None,
    license: str | None = None,
    imported_on: date | None = None,
) -> DocumentDraft:
    """A knowledge document built directly; `sections` maps heading to body."""
    sections = sections or {"Watering": "Water when the soil is dry."}
    document_id = uuid.uuid5(uuid.NAMESPACE_URL, slug)
    content = repr((title, species, license, sorted(sections.items())))
    return DocumentDraft(
        id=document_id,
        slug=slug,
        title=title or slug.title(),
        species=species,
        source_title="PlantSolve: Test plant",
        source_url=f"https://example.test/{slug}",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        sections=[
            SectionDraft(
                uuid.uuid5(document_id, anchor_of(heading)),
                position,
                anchor_of(heading),
                heading,
                body,
            )
            for position, (heading, body) in enumerate(sections.items(), start=1)
        ],
        license=license,
        imported_on=imported_on,
    )


def store_documents(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], *documents: DocumentDraft
) -> None:
    for document in documents:
        with unit_of_work() as uow:
            uow.knowledge.sync(document)
            uow.commit()


def basil_guide() -> DocumentDraft:
    """Five Basil sections, so that a question always gets the four sections a chat turn cites."""
    return make_document(
        "basil",
        "Basil",
        {
            "Pinching and pruning": "Pinch the tips of basil above a leaf pair to keep it bushy.",
            "Feeding": "Feed basil with a mild fertiliser every two weeks while it grows.",
            "Watering": "Water basil when the top of the soil is dry.",
            "Light": "Basil needs six hours of sun each day.",
            "Harvesting": "Harvest basil leaves often so that it does not flower.",
        },
        title="Basil guide",
        license="CC BY 4.0",
    )
