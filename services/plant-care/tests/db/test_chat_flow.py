import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.models import ChatTurnModel, ChatTurnSourceModel
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.chat import ChatService
from app.service.chat_model import ChatOutcome, NewChatTurn
from app.service.diagnosis_model import ModelUnavailable
from app.service.effort import EffortPolicy
from app.service.errors import AiQuotaExhausted
from app.service.knowledge_seed import KnowledgeSeeder
from app.service.model import ModelEffort, Tier
from tests.db.factories import add_journal
from tests.unit.chat_fakes import FakeChatModel, answered
from tests.unit.fakes import FakeAiQuota
from tests.unit.scenario import StaticTiers

KNOWLEDGE_DIR = Path(__file__).parent.parent.parent / "knowledge"
USER = uuid.uuid4()


class CheckingQuota(FakeAiQuota):
    """Records how many turns were already committed when the reservation was committed."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine
        self.turns_at_commit: list[int] = []

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        with Session(self._engine) as session:
            stored = session.scalar(
                select(func.count())
                .select_from(ChatTurnModel)
                .where(ChatTurnModel.ai_reservation_id == reservation_id)
            )
        self.turns_at_commit.append(stored)
        super().commit(reservation_id, user_id)


@pytest.fixture
def journal_id(engine: Engine) -> uuid.UUID:
    with Session(engine) as session:
        values = add_journal(session, user_id=USER, species="Basil")
        session.commit()
    return values["id"]


@pytest.fixture
def quota(engine: Engine) -> CheckingQuota:
    return CheckingQuota(engine)


@pytest.fixture
def service_for(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], quota: CheckingQuota
) -> Callable[[FakeChatModel], ChatService]:
    KnowledgeSeeder(unit_of_work, KNOWLEDGE_DIR).run()

    def build(model: FakeChatModel) -> ChatService:
        return ChatService(
            unit_of_work, quota, model, EffortPolicy(StaticTiers(Tier.STARTER)), 4, 3, 20, 100
        )

    return build


def count(engine: Engine, model) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_a_question_is_answered_from_the_shipped_knowledge_base_and_saved_before_payment(
    service_for, journal_id: uuid.UUID, engine: Engine, quota: CheckingQuota
):
    model = FakeChatModel(answered("S1"))

    reply = service_for(model).ask(
        USER, journal_id, uuid.uuid4(), "How do I pinch the tips of my basil?"
    )

    (request,) = model.requests
    assert "Pinching and pruning" in [section.heading for section in request.sections]
    assert len(request.sections) == 4 and request.effort is ModelEffort.MEDIUM
    assert reply.created and reply.turn is not None
    assert reply.turn.outcome is ChatOutcome.ANSWERED
    (source,) = reply.turn.sources
    assert source.section_id == request.sections[0].id
    assert count(engine, ChatTurnModel) == 1 and count(engine, ChatTurnSourceModel) == 1
    assert quota.turns_at_commit == [1] and quota.committed == quota.reserved


def test_a_retried_request_returns_the_stored_turn_and_spends_nothing_more(
    service_for, journal_id: uuid.UUID, engine: Engine, quota: CheckingQuota
):
    service = service_for(FakeChatModel())
    turn_id = uuid.uuid4()
    first = service.ask(USER, journal_id, turn_id, "How often should I feed basil?")

    again = service.ask(USER, journal_id, turn_id, "How often should I feed basil?")

    assert again.turn == first.turn and not again.created
    assert count(engine, ChatTurnModel) == 1
    assert len(quota.reserved) == 1 and len(quota.committed) == 1


def test_a_model_failure_leaves_the_database_and_the_quota_untouched(
    service_for, journal_id: uuid.UUID, engine: Engine, quota: CheckingQuota
):
    reply = service_for(FakeChatModel(ModelUnavailable("timeout"))).ask(
        USER, journal_id, uuid.uuid4(), "How often should I feed basil?"
    )

    assert reply.turn is None and len(reply.related) == 4
    assert count(engine, ChatTurnModel) == 0
    assert quota.released == quota.reserved and quota.committed == []


def test_an_exhausted_quota_blocks_the_question_before_anything_is_stored(
    service_for, journal_id: uuid.UUID, engine: Engine, quota: CheckingQuota
):
    quota.exhausted = True
    model = FakeChatModel()

    with pytest.raises(AiQuotaExhausted):
        service_for(model).ask(USER, journal_id, uuid.uuid4(), "Water?")

    assert model.requests == [] and count(engine, ChatTurnModel) == 0


def test_a_duplicate_stored_while_the_model_runs_wins_and_the_loser_is_released(
    service_for,
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork],
    journal_id: uuid.UUID,
    engine: Engine,
    quota: CheckingQuota,
):
    turn_id = uuid.uuid4()
    model = FakeChatModel()

    def other_request_stores_the_turn() -> None:
        with unit_of_work() as uow:
            uow.chat_turns.add(
                NewChatTurn(
                    turn_id,
                    journal_id,
                    "Water?",
                    ChatOutcome.NOT_IN_KB,
                    "From the winner.",
                    uuid.uuid4(),
                    ModelEffort.MEDIUM,
                    [],
                )
            )
            uow.commit()

    model.during_call = other_request_stores_the_turn

    reply = service_for(model).ask(USER, journal_id, turn_id, "Water?")

    assert reply.turn is not None and reply.turn.answer == "From the winner."
    assert not reply.created
    assert count(engine, ChatTurnModel) == 1 and count(engine, ChatTurnSourceModel) == 0
    assert quota.released == quota.reserved and quota.committed == []


def test_the_history_sent_to_the_model_comes_from_the_journal(service_for, journal_id: uuid.UUID):
    service = service_for(FakeChatModel())
    for number in range(4):
        service.ask(USER, journal_id, uuid.uuid4(), f"question {number}")
    model = FakeChatModel()

    service_for(model).ask(USER, journal_id, uuid.uuid4(), "latest question")

    assert [turn.question for turn in model.requests[0].history] == [
        "question 1",
        "question 2",
        "question 3",
    ]
