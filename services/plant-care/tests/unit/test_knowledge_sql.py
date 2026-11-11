import uuid

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config import Settings
from app.models import (
    ChatTurnModel,
    ChatTurnSourceModel,
    KbDocumentModel,
    KbSectionModel,
)
from app.repository.knowledge import SqlAlchemyKnowledgeRepository
from tests.unit.settings import make_settings

DIALECT = postgresql.dialect()
HOSTILE = "it's & ( | ! :* <-> \\ http://a.test/b?c=(d)&e"


class Result:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def scalar_one(self):
        return self.rows[0]

    def __iter__(self):
        return iter(self.rows)


class RecordingSession:
    """Answers the lexeme query with canned lexemes and records every statement."""

    def __init__(self, lexemes: list[str]) -> None:
        self.lexemes = lexemes
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return Result([self.lexemes] if len(self.statements) == 1 else [])

    def compiled(self, index: int):
        return self.statements[index].compile(dialect=DIALECT)


def ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=DIALECT))


def test_the_generated_search_column_and_its_gin_index_follow_the_design():
    table = ddl(KbSectionModel)
    (index,) = [
        i for i in KbSectionModel.__table__.indexes if i.name == "ix_kb_sections_search_vector"
    ]

    assert (
        "search_vector TSVECTOR GENERATED ALWAYS AS (setweight(to_tsvector('english', heading)"
        in table
    )
    assert "setweight(to_tsvector('english', body), 'B')) STORED" in table
    assert "USING gin (search_vector)" in str(CreateIndex(index).compile(dialect=DIALECT))


def test_constraints_carry_the_names_of_the_design():
    assert "CONSTRAINT pk_kb_documents PRIMARY KEY (id)" in ddl(KbDocumentModel)
    assert "CONSTRAINT uq_kb_documents_slug UNIQUE (slug)" in ddl(KbDocumentModel)
    assert "CONSTRAINT ck_kb_documents_source_url CHECK" in ddl(KbDocumentModel)
    sections = ddl(KbSectionModel)
    assert "CONSTRAINT uq_kb_sections_document_position UNIQUE (document_id, position)" in sections
    assert "CONSTRAINT uq_kb_sections_document_anchor UNIQUE (document_id, anchor)" in sections
    assert "CONSTRAINT fk_kb_sections_document_id_kb_documents FOREIGN KEY" in sections
    turns = ddl(ChatTurnModel)
    assert "CONSTRAINT uq_chat_turns_ai_reservation_id UNIQUE (ai_reservation_id)" in turns
    assert "CONSTRAINT fk_chat_turns_journal_id_journals FOREIGN KEY" in turns
    assert "CONSTRAINT ck_chat_turns_outcome CHECK" in turns
    sources = ddl(ChatTurnSourceModel)
    assert "CONSTRAINT pk_chat_turn_sources PRIMARY KEY (turn_id, rank)" in sources
    assert "CONSTRAINT fk_chat_turn_sources_turn_id_chat_turns FOREIGN KEY" in sources
    assert "section_id UUID NOT NULL" in sources and "REFERENCES kb_sections" not in sources


def test_the_user_text_reaches_the_database_only_as_a_bound_parameter():
    session = RecordingSession(["it", "http://a.test/b?c=(d)&e"])
    found = SqlAlchemyKnowledgeRepository(session).search(HOSTILE, "Basil", 4)

    assert found == []
    lexeme_query = session.compiled(0)
    assert HOSTILE not in str(lexeme_query)
    assert HOSTILE in lexeme_query.params.values()
    match = session.compiled(1)
    assert "'it' | 'http://a.test/b?c=(d)&e'" in match.params.values()
    assert "http" not in str(match)
    assert "CAST(" in str(match) and "AS TSQUERY" in str(match)
    assert "to_tsquery" not in str(match).lower().replace("cast", "")


def test_the_match_query_filters_by_species_ranks_and_limits():
    session = RecordingSession(["basil"])
    SqlAlchemyKnowledgeRepository(session).search("basil", "Basil", 3)

    sql = str(session.compiled(1))

    assert "kb_sections.search_vector @@ CAST(" in sql
    assert "kb_documents.species IS NULL OR lower(kb_documents.species) = lower(" in sql
    assert "ORDER BY ts_rank_cd(kb_sections.search_vector, CAST(" in sql
    assert "kb_documents.species IS NULL, kb_documents.slug, kb_sections.position" in sql
    assert session.compiled(1).params["param_1"] == 3


def test_without_lexemes_only_the_species_sections_are_fetched_in_document_order():
    session = RecordingSession([])

    SqlAlchemyKnowledgeRepository(session).search("the and of", "Basil", 4)

    assert len(session.statements) == 2
    sql = str(session.compiled(1))
    assert "@@" not in sql
    assert "lower(kb_documents.species) = lower(" in sql
    assert "kb_documents.species IS NULL" not in sql
    assert "ORDER BY kb_documents.slug, kb_sections.position" in sql


def test_the_top_up_skips_sections_that_were_already_found():
    taken = uuid.uuid4()
    session = RecordingSession([])

    SqlAlchemyKnowledgeRepository(session).search("", "Basil", 4)
    SqlAlchemyKnowledgeRepository(session)._top_up("Basil", 2, {taken})

    sql = session.compiled(2)
    assert "kb_sections.id NOT IN" in str(sql)


@pytest.mark.parametrize("top_k", [0, 5, -1])
def test_the_top_k_is_limited_to_the_citation_ranks(top_k: int):
    with pytest.raises(ValueError, match="chat_top_k"):
        make_settings(chat_top_k=top_k)


def test_the_defaults_are_four_sections_from_the_knowledge_directory():
    settings: Settings = make_settings()

    assert settings.chat_top_k == 4
    assert str(settings.knowledge_dir) == "knowledge"
