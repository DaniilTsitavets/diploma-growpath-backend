import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

from app.service.chat_model import ChatPage, ChatTurn, Citation
from app.service.knowledge_model import RetrievedSection

ChatTurnOutcome = Literal["answered", "not_in_kb", "out_of_scope", "sources_only"]


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    question: str


class SourceResponse(BaseModel):
    rank: int
    section_id: uuid.UUID
    document_title: str
    section_heading: str
    source_url: str

    @classmethod
    def of(cls, citation: Citation) -> Self:
        return cls(**vars(citation))


class RelatedSectionResponse(BaseModel):
    section_id: uuid.UUID
    document_title: str
    section_heading: str
    source_url: str
    body: str

    @classmethod
    def of(cls, section: RetrievedSection) -> Self:
        return cls(
            section_id=section.id,
            document_title=section.document_title,
            section_heading=section.heading,
            source_url=section.source_url,
            body=section.body,
        )


class ChatTurnResponse(BaseModel):
    """A stored turn, or `sources_only`: the sections found, with no answer and nothing stored."""

    id: uuid.UUID
    journal_id: uuid.UUID
    question: str
    outcome: ChatTurnOutcome
    answer: str | None
    model_effort: str | None
    created_at: datetime | None
    sources: list[SourceResponse]
    related: list[RelatedSectionResponse]

    @classmethod
    def of_turn(cls, turn: ChatTurn) -> Self:
        return cls(
            id=turn.id,
            journal_id=turn.journal_id,
            question=turn.question,
            outcome=turn.outcome.value,
            answer=turn.answer,
            model_effort=turn.model_effort.value,
            created_at=turn.created_at,
            sources=[SourceResponse.of(source) for source in turn.sources],
            related=[],
        )

    @classmethod
    def sources_only(
        cls,
        journal_id: uuid.UUID,
        request: ChatTurnRequest,
        sections: list[RetrievedSection],
    ) -> Self:
        return cls(
            id=request.id,
            journal_id=journal_id,
            question=request.question,
            outcome="sources_only",
            answer=None,
            model_effort=None,
            created_at=None,
            sources=[],
            related=[RelatedSectionResponse.of(section) for section in sections],
        )


class ChatHistoryResponse(BaseModel):
    items: list[ChatTurnResponse]
    next_cursor: str | None

    @classmethod
    def of(cls, page: ChatPage) -> Self:
        return cls(
            items=[ChatTurnResponse.of_turn(turn) for turn in page.items],
            next_cursor=page.next_cursor,
        )
