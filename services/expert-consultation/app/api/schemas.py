import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.service.expert_tickets import QueuePage, TicketView
from app.service.model import ANSWER_LIMIT, BLANK_CHARACTERS, Ticket, TicketStatus, TicketSummary
from app.service.photos import PhotoLink


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, Field(max_length=ANSWER_LIMIT)]

    @field_validator("body")
    @classmethod
    def _require_text(cls, body: str) -> str:
        if not body.strip(BLANK_CHARACTERS):
            raise ValueError("the answer must not be blank")
        return body


class AnswerResponse(BaseModel):
    ticket_id: uuid.UUID
    status: TicketStatus
    answered_at: datetime | None
    answer_text: str | None

    @classmethod
    def of(cls, ticket: Ticket) -> "AnswerResponse":
        return cls(
            ticket_id=ticket.id,
            status=ticket.status,
            answered_at=ticket.answered_at,
            answer_text=ticket.answer.body if ticket.answer else None,
        )


class TicketSummaryResponse(BaseModel):
    id: uuid.UUID
    species: str | None
    created_at: datetime
    expires_at: datetime

    @classmethod
    def of(cls, summary: TicketSummary) -> "TicketSummaryResponse":
        return cls(
            id=summary.id,
            species=summary.species,
            created_at=summary.created_at,
            expires_at=summary.expires_at,
        )


class QueueResponse(BaseModel):
    items: list[TicketSummaryResponse]
    next_cursor: str | None

    @classmethod
    def of(cls, page: QueuePage) -> "QueueResponse":
        return cls(
            items=[TicketSummaryResponse.of(item) for item in page.items],
            next_cursor=page.next_cursor,
        )


class PhotoResponse(BaseModel):
    kind: str
    url: str

    @classmethod
    def of(cls, link: PhotoLink) -> "PhotoResponse":
        return cls(kind=link.kind, url=link.url)


class TicketResponse(BaseModel):
    id: uuid.UUID
    status: TicketStatus
    created_at: datetime
    expires_at: datetime
    answered_at: datetime | None
    cancelled_at: datetime | None
    context: dict[str, Any]
    photos: list[PhotoResponse]
    answer_text: str | None

    @classmethod
    def of(cls, view: TicketView) -> "TicketResponse":
        ticket = view.ticket
        return cls(
            id=ticket.id,
            status=ticket.status,
            created_at=ticket.created_at,
            expires_at=ticket.expires_at,
            answered_at=ticket.answered_at,
            cancelled_at=ticket.cancelled_at,
            context=ticket.context,
            photos=[PhotoResponse.of(link) for link in view.photos],
            answer_text=ticket.answer.body if ticket.answer else None,
        )
