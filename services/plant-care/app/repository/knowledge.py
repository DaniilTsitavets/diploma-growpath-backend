import uuid

from sqlalchemy import (
    Boolean,
    Text,
    bindparam,
    cast,
    delete,
    func,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import TSQUERY, insert
from sqlalchemy.orm import Session

from app.models import KbDocumentModel, KbSectionModel
from app.service.knowledge_model import DocumentDraft, RetrievedSection, SyncResult
from app.service.tsquery import build_tsquery

ENGLISH = literal_column("'english'::regconfig")
SECTION_COLUMNS = (
    KbSectionModel.id,
    KbDocumentModel.title.label("document_title"),
    KbDocumentModel.source_url,
    KbSectionModel.heading,
    KbSectionModel.body,
)


class SqlAlchemyKnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def sync(self, document: DocumentDraft) -> SyncResult:
        """Insert the document, or replace its sections when the file content changed.

        The guarded UPDATE takes the row lock first, so a second replica that starts at the same
        time waits, then finds the hash already current and does nothing.
        """
        fields = {
            "slug": document.slug,
            "title": document.title,
            "species": document.species,
            "source_title": document.source_title,
            "source_url": document.source_url,
            "content_sha256": document.content_sha256,
        }
        inserted = self._session.execute(
            insert(KbDocumentModel)
            .values(id=document.id, **fields)
            .on_conflict_do_nothing(index_elements=[KbDocumentModel.id])
            .returning(KbDocumentModel.id)
        ).scalar_one_or_none()
        if inserted is not None:
            self._insert_sections(document)
            return SyncResult.CREATED
        changed = self._session.execute(
            update(KbDocumentModel)
            .where(
                KbDocumentModel.id == document.id,
                KbDocumentModel.content_sha256 != document.content_sha256,
            )
            .values(**fields)
            .returning(KbDocumentModel.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if changed is None:
            return SyncResult.UNCHANGED
        self._session.execute(
            delete(KbSectionModel).where(KbSectionModel.document_id == document.id)
        )
        self._insert_sections(document)
        return SyncResult.UPDATED

    def search(self, question: str, species: str, limit: int) -> list[RetrievedSection]:
        """Sections matching any word of the question, best first; topped up from the species' own.

        Sections of the journal's species and general ones are searched; on equal rank the
        species' section comes first. Only the species' sections fill the remaining places.
        """
        tsquery = build_tsquery(self._lexemes(question))
        found = [] if tsquery is None else self._match(tsquery, species, limit)
        if len(found) < limit:
            found += self._top_up(species, limit - len(found), {section.id for section in found})
        return found

    def _insert_sections(self, document: DocumentDraft) -> None:
        self._session.execute(
            insert(KbSectionModel),
            [
                {
                    "id": section.id,
                    "document_id": document.id,
                    "position": section.position,
                    "anchor": section.anchor,
                    "heading": section.heading,
                    "body": section.body,
                }
                for section in document.sections
            ],
        )

    def _lexemes(self, question: str) -> list[str]:
        vector = func.to_tsvector(ENGLISH, cast(question, Text))
        return self._session.execute(select(func.tsvector_to_array(vector))).scalar_one()

    def _match(self, tsquery: str, species: str, limit: int) -> list[RetrievedSection]:
        query = cast(bindparam("tsquery", tsquery, type_=Text), TSQUERY)
        rows = self._session.execute(
            select(*SECTION_COLUMNS)
            .join(KbDocumentModel, KbDocumentModel.id == KbSectionModel.document_id)
            .where(
                KbSectionModel.search_vector.op("@@", return_type=Boolean)(query),
                or_(KbDocumentModel.species.is_(None), _species_matches(species)),
            )
            .order_by(
                func.ts_rank_cd(KbSectionModel.search_vector, query).desc(),
                KbDocumentModel.species.is_(None),
                KbDocumentModel.slug,
                KbSectionModel.position,
            )
            .limit(limit)
        )
        return [RetrievedSection(**row._mapping) for row in rows]

    def _top_up(self, species: str, limit: int, taken: set[uuid.UUID]) -> list[RetrievedSection]:
        rows = self._session.execute(
            select(*SECTION_COLUMNS)
            .join(KbDocumentModel, KbDocumentModel.id == KbSectionModel.document_id)
            .where(_species_matches(species), KbSectionModel.id.not_in(taken))
            .order_by(KbDocumentModel.slug, KbSectionModel.position)
            .limit(limit)
        )
        return [RetrievedSection(**row._mapping) for row in rows]


def _species_matches(species: str):
    return func.lower(KbDocumentModel.species) == func.lower(species)
