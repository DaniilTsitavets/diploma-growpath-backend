import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.service.knowledge_model import RetrievedSection
from app.service.model import ModelEffort


class ChatOutcome(StrEnum):
    ANSWERED = "answered"
    NOT_IN_KB = "not_in_kb"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class Citation:
    """A knowledge-base section the answer relies on, copied so that reseeding cannot change it."""

    rank: int
    section_id: uuid.UUID
    document_title: str
    section_heading: str
    source_url: str


@dataclass(frozen=True)
class NewChatTurn:
    id: uuid.UUID
    journal_id: uuid.UUID
    question: str
    outcome: ChatOutcome
    answer: str
    ai_reservation_id: uuid.UUID
    model_effort: ModelEffort
    sources: list[Citation]


@dataclass(frozen=True)
class ChatTurn:
    id: uuid.UUID
    journal_id: uuid.UUID
    question: str
    outcome: ChatOutcome
    answer: str
    model_effort: ModelEffort
    created_at: datetime
    sources: list[Citation]


@dataclass(frozen=True)
class PriorTurn:
    question: str
    answer: str


@dataclass(frozen=True)
class ChatRequest:
    question: str
    species: str
    sections: list[RetrievedSection]
    history: list[PriorTurn]
    """Oldest first."""
    effort: ModelEffort


@dataclass(frozen=True)
class ModelChatAnswer:
    """The model's verdict; `cited` holds unchecked section references such as `S1`."""

    outcome: ChatOutcome
    answer: str
    cited: list[str]


@dataclass(frozen=True)
class ChatReply:
    """A stored turn; without one, the sections found for a question that got no answer."""

    turn: ChatTurn | None
    created: bool
    related: list[RetrievedSection]


@dataclass(frozen=True)
class ChatPage:
    items: list[ChatTurn]
    next_cursor: str | None


class ChatModel(Protocol):
    def answer(self, request: ChatRequest) -> ModelChatAnswer:
        """Raises ModelFailure."""
        ...
