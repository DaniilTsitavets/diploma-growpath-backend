import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ChatTurnSourceModel
from app.repository.chat_turns import SqlAlchemyChatTurnRepository
from app.service.chat_model import ChatOutcome, Citation, NewChatTurn
from app.service.model import FeedCursor, ModelEffort
from tests.db.chat_factories import add_chat_turn
from tests.db.factories import add_journal

T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def new_turn(journal_id: uuid.UUID, sources: list[Citation] | None = None, **fields) -> NewChatTurn:
    values = {
        "id": uuid.uuid4(),
        "journal_id": journal_id,
        "question": "How often to water?",
        "outcome": ChatOutcome.ANSWERED,
        "answer": "When dry.",
        "ai_reservation_id": uuid.uuid4(),
        "model_effort": ModelEffort.HIGH,
        "sources": sources or [],
    }
    return NewChatTurn(**{**values, **fields})


def citation(rank: int, heading: str = "Watering") -> Citation:
    return Citation(rank, uuid.uuid4(), "Basil guide", heading, "https://example.test/basil")


@pytest.fixture
def journal_id(session: Session) -> uuid.UUID:
    return add_journal(session)["id"]


def test_a_new_turn_is_stored_with_its_citations_in_rank_order(
    session: Session, journal_id: uuid.UUID
):
    repository = SqlAlchemyChatTurnRepository(session)
    turn = new_turn(journal_id, [citation(2, "Light"), citation(1, "Watering")])

    created = repository.add(turn)
    stored = repository.get(turn.id)

    assert created
    assert stored is not None
    assert (stored.id, stored.journal_id, stored.question) == (turn.id, journal_id, turn.question)
    assert (stored.outcome, stored.answer) == (ChatOutcome.ANSWERED, "When dry.")
    assert stored.model_effort is ModelEffort.HIGH and stored.created_at is not None
    assert [(s.rank, s.section_heading) for s in stored.sources] == [(1, "Watering"), (2, "Light")]
    assert stored.sources[0].document_title == "Basil guide"
    assert stored.sources[0].source_url == "https://example.test/basil"


def test_a_turn_without_citations_is_stored(session: Session, journal_id: uuid.UUID):
    repository = SqlAlchemyChatTurnRepository(session)
    turn = new_turn(journal_id, outcome=ChatOutcome.OUT_OF_SCOPE)

    assert repository.add(turn)

    stored = repository.get(turn.id)
    assert (
        stored is not None and stored.sources == [] and stored.outcome is ChatOutcome.OUT_OF_SCOPE
    )


def test_adding_a_stored_id_again_changes_nothing_and_skips_the_citations(
    session: Session, journal_id: uuid.UUID
):
    repository = SqlAlchemyChatTurnRepository(session)
    first = new_turn(journal_id, [citation(1)])
    repository.add(first)
    again = new_turn(journal_id, [citation(1, "Other"), citation(2)], id=first.id, answer="Other")

    created = repository.add(again)

    stored = repository.get(first.id)
    assert not created
    assert stored is not None and stored.answer == "When dry."
    assert [s.section_heading for s in stored.sources] == ["Watering"]
    assert session.scalar(select(func.count()).select_from(ChatTurnSourceModel)) == 1


def test_an_unknown_turn_is_none(session: Session):
    assert SqlAlchemyChatTurnRepository(session).get(uuid.uuid4()) is None


def test_the_page_lists_the_newest_turns_of_the_journal_first(
    session: Session, journal_id: uuid.UUID
):
    for number in range(4):
        add_chat_turn(
            session, journal_id, question=f"q{number}", created_at=T0 + timedelta(minutes=number)
        )
    add_chat_turn(session, question="other journal", created_at=T0 + timedelta(hours=1))

    page = SqlAlchemyChatTurnRepository(session).page(journal_id, 3, None)

    assert [turn.question for turn in page] == ["q3", "q2", "q1"]


def test_the_page_continues_strictly_before_the_cursor(session: Session, journal_id: uuid.UUID):
    repository = SqlAlchemyChatTurnRepository(session)
    for number in range(4):
        add_chat_turn(
            session, journal_id, question=f"q{number}", created_at=T0 + timedelta(minutes=number)
        )
    first = repository.page(journal_id, 2, None)

    last = first[-1]
    second = repository.page(journal_id, 2, FeedCursor(last.created_at, last.id))

    assert [turn.question for turn in first + second] == ["q3", "q2", "q1", "q0"]


def test_turns_created_at_the_same_instant_are_ordered_by_id_without_gaps(
    session: Session, journal_id: uuid.UUID
):
    repository = SqlAlchemyChatTurnRepository(session)
    ids = sorted(add_chat_turn(session, journal_id, created_at=T0)["id"] for _ in range(4))

    first = repository.page(journal_id, 2, None)
    second = repository.page(journal_id, 2, FeedCursor(first[-1].created_at, first[-1].id))

    assert [turn.id for turn in first + second] == list(reversed(ids))


def test_every_turn_of_a_page_carries_its_own_citations(session: Session, journal_id: uuid.UUID):
    repository = SqlAlchemyChatTurnRepository(session)
    older = new_turn(journal_id, [citation(1, "Light")])
    newer = new_turn(journal_id, [citation(1, "Watering"), citation(2, "Feeding")])
    repository.add(older)
    repository.add(newer)

    page = repository.page(journal_id, 10, None)

    by_id = {turn.id: [s.section_heading for s in turn.sources] for turn in page}
    assert by_id == {older.id: ["Light"], newer.id: ["Watering", "Feeding"]}


def test_an_empty_journal_has_an_empty_page(session: Session, journal_id: uuid.UUID):
    assert SqlAlchemyChatTurnRepository(session).page(journal_id, 5, None) == []


def test_a_zero_limit_returns_no_turns(session: Session, journal_id: uuid.UUID):
    add_chat_turn(session, journal_id)

    assert SqlAlchemyChatTurnRepository(session).page(journal_id, 0, None) == []
