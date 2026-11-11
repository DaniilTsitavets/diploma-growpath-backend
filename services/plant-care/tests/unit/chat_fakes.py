import uuid
from collections.abc import Callable
from datetime import timedelta

from app.service.chat import ChatService
from app.service.chat_model import (
    ChatOutcome,
    ChatRequest,
    ChatTurn,
    Citation,
    ModelChatAnswer,
    NewChatTurn,
)
from app.service.effort import EffortPolicy
from app.service.model import FeedCursor, ModelEffort, Tier
from tests.unit.fakes import NOW, FakeAiQuota, FakeUnitOfWork
from tests.unit.knowledge_fakes import FakeKnowledge, make_section
from tests.unit.scenario import USER, StaticTiers


class FakeChatTurns:
    def __init__(self) -> None:
        self.turns: dict[uuid.UUID, ChatTurn] = {}
        self.added: list[NewChatTurn] = []

    def seed(
        self, journal_id: uuid.UUID, question: str = "q", answer: str = "a", **fields
    ) -> ChatTurn:
        values = {
            "id": uuid.uuid4(),
            "journal_id": journal_id,
            "question": question,
            "outcome": ChatOutcome.ANSWERED,
            "answer": answer,
            "model_effort": ModelEffort.HIGH,
            "created_at": NOW + timedelta(minutes=len(self.turns)),
            "sources": [],
        }
        turn = ChatTurn(**{**values, **fields})
        self.turns[turn.id] = turn
        return turn

    def add(self, turn: NewChatTurn) -> bool:
        if turn.id in self.turns:
            return False
        self.added.append(turn)
        self.seed(
            turn.journal_id,
            turn.question,
            turn.answer,
            id=turn.id,
            outcome=turn.outcome,
            model_effort=turn.model_effort,
            sources=turn.sources,
        )
        return True

    def get(self, turn_id: uuid.UUID) -> ChatTurn | None:
        return self.turns.get(turn_id)

    def page(self, journal_id: uuid.UUID, limit: int, before: FeedCursor | None) -> list[ChatTurn]:
        rows = [turn for turn in self.turns.values() if turn.journal_id == journal_id]
        rows.sort(key=lambda turn: (turn.created_at, turn.id), reverse=True)
        if before is not None:
            rows = [
                turn for turn in rows if (turn.created_at, turn.id) < (before.created_at, before.id)
            ]
        return rows[:limit]


class ChatUnitOfWork(FakeUnitOfWork):
    """The fake unit of work with the knowledge base and the chat turns; it logs its commits."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.knowledge = FakeKnowledge()
        self.chat_turns = FakeChatTurns()
        self.events = events

    def commit(self) -> None:
        super().commit()
        self.events.append("db-commit")


class RecordingAiQuota(FakeAiQuota):
    """A fake quota that writes its calls to the log shared with the unit of work."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        super().reserve(reservation_id, user_id)
        self.events.append("reserve")

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        super().commit(reservation_id, user_id)
        self.events.append("commit")

    def release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        super().release(reservation_id, user_id)
        self.events.append("release")


def answered(*cited: str, text: str = "Water when dry.") -> ModelChatAnswer:
    return ModelChatAnswer(ChatOutcome.ANSWERED, text, list(cited))


def make_chat_request(**overrides) -> ChatRequest:
    fields = {
        "question": "How often to water?",
        "species": "Basil",
        "sections": [make_section(1), make_section(2)],
        "history": [],
        "effort": ModelEffort.HIGH,
    }
    return ChatRequest(**{**fields, **overrides})


def citation(rank: int = 1) -> Citation:
    section = make_section(rank)
    return Citation(rank, section.id, section.document_title, section.heading, section.source_url)


class FakeChatModel:
    """Plays its script answer by answer; an exception in the script is raised."""

    def __init__(self, *script: ModelChatAnswer | Exception) -> None:
        self.script = list(script) or [answered("S1")]
        self.requests: list[ChatRequest] = []
        self.during_call: Callable[[], None] | None = None

    def answer(self, request: ChatRequest) -> ModelChatAnswer:
        self.requests.append(request)
        if self.during_call is not None:
            self.during_call()
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step


class ChatScenario:
    """A user with a basil journal and a knowledge base that finds two sections."""

    def __init__(self, tier: Tier | None = Tier.HOBBYIST) -> None:
        self.events: list[str] = []
        self.uow = ChatUnitOfWork(self.events)
        self.quota = RecordingAiQuota(self.events)
        self.tiers = StaticTiers(tier)
        self.journal = self.uow.journals.seed(USER, species="Basil")
        self.sections = [make_section(1), make_section(2)]
        self.uow.knowledge.found = self.sections

    def service(self, model: FakeChatModel, top_k: int = 4, history_turns: int = 3) -> ChatService:
        return ChatService(
            lambda: self.uow,
            self.quota,
            model,
            EffortPolicy(self.tiers),
            top_k,
            history_turns,
            default_page_size=2,
            max_page_size=3,
        )

    def ask(self, service: ChatService, question: str = "How often to water?", turn_id=None):
        return service.ask(USER, self.journal.id, turn_id or uuid.uuid4(), question)
