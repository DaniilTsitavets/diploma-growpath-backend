import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import KbSectionModel
from tests.db.chat_factories import (
    add_chat_source,
    add_chat_turn,
    add_kb_document,
    add_kb_section,
)
from tests.db.factories import add_journal, violated


def rejected_by(session: Session, constraint: str, add, **fields):
    with violated(session) as violation:
        add(session, **fields)

    assert violation.constraint == constraint


@pytest.mark.parametrize("slug", ["", "Basil", "basil plant", "basil_plant", "x" * 65])
def test_a_document_slug_is_lowercase_ascii_with_dashes(session: Session, slug: str):
    rejected_by(session, "ck_kb_documents_slug", add_kb_document, slug=slug)


def test_a_document_slug_is_unique(session: Session):
    add_kb_document(session, slug="basil")

    rejected_by(session, "uq_kb_documents_slug", add_kb_document, slug="basil")


@pytest.mark.parametrize("url", ["http://example.test/a", "example.test/a", "ftp://example.test"])
def test_a_document_source_must_be_https(session: Session, url: str):
    rejected_by(session, "ck_kb_documents_source_url", add_kb_document, source_url=url)


@pytest.mark.parametrize("sha", ["", "abc", "G" * 64, "A" * 64, "0" * 63])
def test_a_document_hash_is_64_lowercase_hex_digits(session: Session, sha: str):
    rejected_by(session, "ck_kb_documents_sha", add_kb_document, content_sha256=sha)


@pytest.mark.parametrize(
    "fields",
    [{"title": "  "}, {"title": "t" * 201}, {"species": "s" * 101}, {"source_title": "x" * 301}],
)
def test_document_texts_are_bounded(session: Session, fields: dict):
    rejected_by(session, "ck_kb_documents_lengths", add_kb_document, **fields)


def test_a_section_needs_an_existing_document(session: Session):
    rejected_by(
        session, "fk_kb_sections_document_id_kb_documents", add_kb_section, document_id=uuid.uuid4()
    )


def test_a_section_position_and_anchor_are_unique_within_a_document(session: Session):
    document_id = add_kb_document(session)["id"]
    add_kb_section(session, document_id)

    rejected_by(
        session,
        "uq_kb_sections_document_position",
        add_kb_section,
        document_id=document_id,
        anchor="other",
    )
    rejected_by(
        session,
        "uq_kb_sections_document_anchor",
        add_kb_section,
        document_id=document_id,
        position=2,
    )


def test_the_same_position_may_repeat_in_another_document(session: Session):
    add_kb_section(session)
    add_kb_section(session)


@pytest.mark.parametrize("position", [0, -1])
def test_a_section_position_starts_at_one(session: Session, position: int):
    rejected_by(session, "ck_kb_sections_position", add_kb_section, position=position)


@pytest.mark.parametrize(
    "fields", [{"heading": " "}, {"heading": "h" * 201}, {"body": ""}, {"body": "b" * 4001}]
)
def test_section_texts_are_non_blank_and_bounded(session: Session, fields: dict):
    rejected_by(session, "ck_kb_sections_lengths", add_kb_section, **fields)


def test_the_database_fills_the_search_vector_with_the_heading_weighted_above_the_body(
    session: Session,
):
    section_id = add_kb_section(session, heading="Watering basil", body="Water the soil.")["id"]

    vector = session.scalar(
        select(KbSectionModel.search_vector).where(KbSectionModel.id == section_id)
    )

    assert "'basil':2A" in vector
    assert "'water':1A" in vector and "B" in vector.split("'soil':")[1]


def test_a_turn_needs_an_existing_journal(session: Session):
    rejected_by(
        session, "fk_chat_turns_journal_id_journals", add_chat_turn, journal_id=uuid.uuid4()
    )


@pytest.mark.parametrize("outcome", ["sources_only", "", "ANSWERED"])
def test_a_stored_turn_has_one_of_the_three_outcomes(session: Session, outcome: str):
    rejected_by(session, "ck_chat_turns_outcome", add_chat_turn, outcome=outcome)


@pytest.mark.parametrize("effort", ["low", "", "max"])
def test_a_turn_effort_is_medium_or_high(session: Session, effort: str):
    rejected_by(session, "ck_chat_turns_model_effort", add_chat_turn, model_effort=effort)


@pytest.mark.parametrize(
    "fields",
    [{"question": " "}, {"question": "q" * 1001}, {"answer": ""}, {"answer": "a" * 4001}],
)
def test_turn_texts_are_non_blank_and_bounded(session: Session, fields: dict):
    rejected_by(session, "ck_chat_turns_lengths", add_chat_turn, **fields)


def test_a_reservation_pays_for_one_turn_only(session: Session):
    reservation_id = add_chat_turn(session)["ai_reservation_id"]

    rejected_by(
        session, "uq_chat_turns_ai_reservation_id", add_chat_turn, ai_reservation_id=reservation_id
    )


def test_a_turn_id_is_unique(session: Session):
    journal_id = add_journal(session)["id"]
    turn_id = add_chat_turn(session, journal_id)["id"]

    rejected_by(session, "pk_chat_turns", add_chat_turn, journal_id=journal_id, id=turn_id)


def test_a_citation_needs_an_existing_turn(session: Session):
    rejected_by(
        session, "fk_chat_turn_sources_turn_id_chat_turns", add_chat_source, turn_id=uuid.uuid4()
    )


@pytest.mark.parametrize("rank", [0, 5, -1])
def test_a_citation_rank_is_between_one_and_four(session: Session, rank: int):
    rejected_by(session, "ck_chat_turn_sources_rank", add_chat_source, rank=rank)


def test_a_rank_is_used_once_per_turn(session: Session):
    turn_id = add_chat_turn(session)["id"]
    add_chat_source(session, turn_id)

    rejected_by(session, "pk_chat_turn_sources", add_chat_source, turn_id=turn_id)


def test_a_citation_survives_the_removal_of_its_section(session: Session):
    section_id = add_kb_section(session)["id"]
    add_chat_source(session, section_id=section_id)

    session.execute(KbSectionModel.__table__.delete().where(KbSectionModel.id == section_id))

    assert session.scalar(select(func.count()).select_from(KbSectionModel)) == 0


@pytest.mark.parametrize(
    "fields",
    [{"document_title": "t" * 201}, {"section_heading": "h" * 201}, {"source_url": "u" * 1001}],
)
def test_citation_texts_are_bounded(session: Session, fields: dict):
    rejected_by(session, "ck_chat_turn_sources_lengths", add_chat_source, **fields)
