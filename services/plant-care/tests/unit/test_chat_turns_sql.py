import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from app.repository.chat_turns import SqlAlchemyChatTurnRepository
from app.service.chat_model import ChatOutcome, Citation, NewChatTurn
from app.service.model import FeedCursor, ModelEffort

DIALECT = postgresql.dialect()


class Result:
    def __init__(self, inserted: uuid.UUID | None = None) -> None:
        self.inserted = inserted

    def scalar_one_or_none(self):
        return self.inserted

    def all(self):
        return []

    def scalars(self):
        return iter([])


class RecordingSession:
    def __init__(self, inserted: bool = True) -> None:
        self.inserted = uuid.uuid4() if inserted else None
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        return Result(self.inserted)

    def sql(self, index: int) -> str:
        return str(self.statements[index][0].compile(dialect=DIALECT))


def make_turn(sources: list[Citation]) -> NewChatTurn:
    return NewChatTurn(
        uuid.uuid4(),
        uuid.uuid4(),
        "Water?",
        ChatOutcome.ANSWERED,
        "When dry.",
        uuid.uuid4(),
        ModelEffort.HIGH,
        sources,
    )


def test_a_new_turn_is_inserted_with_a_conflict_clause_and_then_its_citations():
    session = RecordingSession()
    source = Citation(1, uuid.uuid4(), "Guide", "Watering", "https://example.test")

    assert SqlAlchemyChatTurnRepository(session).add(make_turn([source]))

    assert "INSERT INTO chat_turns" in session.sql(0)
    assert "ON CONFLICT (id) DO NOTHING RETURNING chat_turns.id" in session.sql(0)
    assert "INSERT INTO chat_turn_sources" in session.sql(1)
    assert session.statements[1][1][0]["rank"] == 1


def test_a_stored_id_inserts_no_citations():
    session = RecordingSession(inserted=False)
    source = Citation(1, uuid.uuid4(), "Guide", "Watering", "https://example.test")

    assert not SqlAlchemyChatTurnRepository(session).add(make_turn([source]))

    assert len(session.statements) == 1


def test_a_turn_without_citations_inserts_none():
    session = RecordingSession()

    SqlAlchemyChatTurnRepository(session).add(make_turn([]))

    assert len(session.statements) == 1


def test_the_page_is_keyset_ordered_newest_first():
    session = RecordingSession()
    cursor = FeedCursor(datetime(2026, 11, 11, tzinfo=UTC), uuid.uuid4())

    SqlAlchemyChatTurnRepository(session).page(uuid.uuid4(), 5, cursor)

    sql = session.sql(0)
    assert "ORDER BY chat_turns.created_at DESC, chat_turns.id DESC" in sql
    assert "(chat_turns.created_at, chat_turns.id) <" in sql
    assert "chat_turns.journal_id =" in sql
