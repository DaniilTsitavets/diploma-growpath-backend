import uuid

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models import ChatTurnModel, ChatTurnSourceModel, KbDocumentModel, KbSectionModel
from tests.db.factories import add_journal

SHA = "0" * 64


def _insert(session: Session, model, values: dict, overrides: dict) -> dict:
    row = {**values, **overrides}
    session.execute(insert(model).values(**row))
    return row


def add_kb_document(session: Session, **overrides) -> dict:
    slug = f"doc-{uuid.uuid4().hex[:8]}"
    values = {
        "id": uuid.uuid4(),
        "slug": slug,
        "title": "A guide",
        "species": None,
        "source_title": "Notes",
        "source_url": "https://example.test/guide",
        "content_sha256": SHA,
    }
    return _insert(session, KbDocumentModel, values, overrides)


def add_kb_section(session: Session, document_id: uuid.UUID | None = None, **overrides) -> dict:
    values = {
        "id": uuid.uuid4(),
        "document_id": document_id or add_kb_document(session)["id"],
        "position": 1,
        "anchor": "watering",
        "heading": "Watering",
        "body": "Water when the soil is dry.",
    }
    return _insert(session, KbSectionModel, values, overrides)


def add_chat_turn(session: Session, journal_id: uuid.UUID | None = None, **overrides) -> dict:
    values = {
        "id": uuid.uuid4(),
        "journal_id": journal_id or add_journal(session)["id"],
        "question": "How often should I water?",
        "outcome": "answered",
        "answer": "When the soil is dry.",
        "ai_reservation_id": uuid.uuid4(),
        "model_effort": "high",
    }
    return _insert(session, ChatTurnModel, values, overrides)


def add_chat_source(session: Session, turn_id: uuid.UUID | None = None, **overrides) -> dict:
    values = {
        "turn_id": turn_id or add_chat_turn(session)["id"],
        "rank": 1,
        "section_id": uuid.uuid4(),
        "document_title": "A guide",
        "section_heading": "Watering",
        "source_url": "https://example.test/guide",
    }
    return _insert(session, ChatTurnSourceModel, values, overrides)
