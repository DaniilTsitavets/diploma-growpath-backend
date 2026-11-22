import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.repository.knowledge import SqlAlchemyKnowledgeRepository
from app.service.knowledge_model import DocumentDraft, SectionDraft, SyncResult


def draft(slug: str, sha: str = "a") -> DocumentDraft:
    document_id = uuid.uuid5(uuid.NAMESPACE_URL, slug)
    return DocumentDraft(
        id=document_id,
        slug=slug,
        title=f"{slug} title",
        species=None,
        source_title="Source",
        source_url="https://www.plantsolve.com/x/",
        content_sha256=sha * 64,
        sections=[
            SectionDraft(uuid.uuid5(document_id, "watering"), 1, "watering", "Watering", "Water.")
        ],
        license="CC BY 4.0",
        imported_on=date(2026, 11, 22),
    )


def repository(session: Session, *documents: DocumentDraft) -> SqlAlchemyKnowledgeRepository:
    repo = SqlAlchemyKnowledgeRepository(session)
    for document in documents:
        assert repo.sync(document) is SyncResult.CREATED
    return repo


def test_slugs_with_prefix_lists_matching_documents_sorted(session: Session):
    repo = repository(
        session,
        draft("plantsolve-lavender"),
        draft("plantsolve-problem-root-rot"),
        draft("plantsolve-aloe-vera"),
        draft("basil"),
    )

    assert repo.slugs_with_prefix("plantsolve-") == [
        "plantsolve-aloe-vera",
        "plantsolve-lavender",
        "plantsolve-problem-root-rot",
    ]


def test_slugs_with_prefix_treats_like_wildcards_literally(session: Session):
    repo = repository(session, draft("plantsolve-lavender"), draft("basil"))

    assert repo.slugs_with_prefix("%") == []
    assert repo.slugs_with_prefix("plantsolve_") == []
    assert repo.slugs_with_prefix("nothing-") == []


def test_content_hashes_returns_the_stored_hash_of_each_known_slug(session: Session):
    repo = repository(session, draft("plantsolve-lavender", "a"), draft("plantsolve-aloe", "b"))

    assert repo.content_hashes(["plantsolve-lavender", "plantsolve-aloe", "unknown"]) == {
        "plantsolve-lavender": "a" * 64,
        "plantsolve-aloe": "b" * 64,
    }


def test_content_hashes_of_nothing_is_empty(session: Session):
    assert SqlAlchemyKnowledgeRepository(session).content_hashes([]) == {}


def test_a_reimport_with_a_new_import_date_but_the_same_hash_keeps_the_stored_date(
    session: Session,
):
    repo = repository(session, draft("plantsolve-lavender"))
    later = draft("plantsolve-lavender")
    later = DocumentDraft(**{**later.__dict__, "imported_on": date(2027, 1, 1)})

    assert repo.sync(later) is SyncResult.UNCHANGED
    assert repo.content_hashes(["plantsolve-lavender"]) == {"plantsolve-lavender": "a" * 64}
