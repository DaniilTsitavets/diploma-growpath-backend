import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Uuid, func, insert, literal, literal_column, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, Select
from sqlalchemy.sql.expression import tuple_

from app.models import AnswerModel, TicketModel
from app.service.model import (
    Answer,
    NewTicket,
    QueueCursor,
    Ticket,
    TicketStatus,
    TicketSummary,
)

OPEN = TicketStatus.OPEN.value
ANSWERED = TicketStatus.ANSWERED.value
CANCELLED = TicketStatus.CANCELLED.value
TICKET_COLUMNS = tuple(TicketModel.__table__.c)
INSERT_COLUMNS = (
    TicketModel.id,
    TicketModel.escalation_id,
    TicketModel.user_id,
    TicketModel.journal_id,
    TicketModel.update_id,
    TicketModel.context,
    TicketModel.expires_at,
)


def to_ticket(row: Mapping[str, Any]) -> Ticket:
    expert_id = row.get("answer_expert_id")
    answer = None if expert_id is None else Answer(expert_id, row["answer_body"])
    return Ticket(
        id=row["id"],
        escalation_id=row["escalation_id"],
        user_id=row["user_id"],
        journal_id=row["journal_id"],
        update_id=row["update_id"],
        context=row["context"],
        status=TicketStatus(row["status"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        answered_at=row["answered_at"],
        cancelled_at=row["cancelled_at"],
        terminal_event_published_at=row["terminal_event_published_at"],
        answer=answer,
    )


def ticket_with_answer() -> Select:
    return (
        select(
            *TICKET_COLUMNS,
            AnswerModel.expert_id.label("answer_expert_id"),
            AnswerModel.body.label("answer_body"),
        )
        .select_from(TicketModel)
        .outerjoin(AnswerModel, AnswerModel.ticket_id == TicketModel.id)
    )


def seconds(value: float) -> ColumnElement:
    """`make_interval(secs => value)`: the function takes the other units positionally."""
    return func.make_interval(*(literal_column("0"),) * 6, literal(float(value), Float))


class SqlAlchemyTicketRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, ticket: NewTicket, ttl_seconds: int, accept_until: datetime) -> bool:
        # The WHERE of the SELECT is the acceptance window: an expired event inserts nothing.
        values = select(
            literal(ticket.id, Uuid()),
            literal(ticket.escalation_id, Uuid()),
            literal(ticket.user_id, Uuid()),
            literal(ticket.journal_id, Uuid()),
            literal(ticket.update_id, Uuid()),
            literal(ticket.context, JSONB()),
            func.now() + seconds(ttl_seconds),
        ).where(func.now() < literal(accept_until, DateTime(timezone=True)))
        statement = (
            pg_insert(TicketModel)
            .from_select(INSERT_COLUMNS, values)
            .on_conflict_do_nothing(index_elements=["escalation_id"])
            .returning(TicketModel.id)
        )
        return self._session.execute(statement).scalar_one_or_none() is not None

    def get(self, ticket_id: uuid.UUID) -> Ticket | None:
        return self._one(ticket_with_answer().where(TicketModel.id == ticket_id))

    def get_by_escalation(self, escalation_id: uuid.UUID) -> Ticket | None:
        return self._one(ticket_with_answer().where(TicketModel.escalation_id == escalation_id))

    def open_queue(self, limit: int, after: QueueCursor | None) -> list[TicketSummary]:
        statement = (
            select(
                TicketModel.id,
                TicketModel.context["species"].astext.label("species"),
                TicketModel.created_at,
                TicketModel.expires_at,
            )
            .where(TicketModel.status == OPEN, TicketModel.expires_at > func.now())
            .order_by(TicketModel.expires_at, TicketModel.id)
            .limit(limit)
        )
        if after is not None:
            statement = statement.where(
                tuple_(TicketModel.expires_at, TicketModel.id) > (after.expires_at, after.id)
            )
        return [TicketSummary(**row._mapping) for row in self._session.execute(statement)]

    def answer(self, ticket_id: uuid.UUID, expert_id: uuid.UUID, body: str) -> bool:
        answered = self._session.execute(
            update(TicketModel)
            .where(
                TicketModel.id == ticket_id,
                TicketModel.status == OPEN,
                TicketModel.expires_at > func.now(),
            )
            .values(status=ANSWERED, answered_at=func.now())
            .returning(TicketModel.id)
            .execution_options(synchronize_session=False)
        )
        if answered.scalar_one_or_none() is None:
            return False
        self._session.execute(
            insert(AnswerModel).values(ticket_id=ticket_id, expert_id=expert_id, body=body)
        )
        return True

    def claim_expired(self, limit: int) -> list[Ticket]:
        due = (
            select(TicketModel.id)
            .where(TicketModel.status == OPEN, TicketModel.expires_at <= func.now())
            .order_by(TicketModel.expires_at, TicketModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("due")
        )
        rows = self._session.execute(
            update(TicketModel)
            .where(TicketModel.id == due.c.id)
            .values(status=CANCELLED, cancelled_at=func.now())
            .returning(*TICKET_COLUMNS)
            .execution_options(synchronize_session=False)
        )
        tickets = [to_ticket(row._mapping) for row in rows]
        return sorted(tickets, key=lambda ticket: (ticket.expires_at, ticket.id))

    def claim_unpublished(self, grace_seconds: float, limit: int) -> list[Ticket]:
        statement = (
            ticket_with_answer()
            .where(
                TicketModel.status != OPEN,
                TicketModel.terminal_event_published_at.is_(None),
                func.coalesce(TicketModel.answered_at, TicketModel.cancelled_at)
                <= func.now() - seconds(grace_seconds),
            )
            .order_by(TicketModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=TicketModel)
        )
        return [to_ticket(row._mapping) for row in self._session.execute(statement)]

    def mark_published(self, ticket_id: uuid.UUID) -> bool:
        result = self._session.execute(
            update(TicketModel)
            .where(TicketModel.id == ticket_id, TicketModel.terminal_event_published_at.is_(None))
            .values(terminal_event_published_at=func.now())
            .returning(TicketModel.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    def _one(self, statement: Select) -> Ticket | None:
        row = self._session.execute(statement).one_or_none()
        return None if row is None else to_ticket(row._mapping)
