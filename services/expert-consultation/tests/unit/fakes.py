import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from growpath_observability import get_correlation_id

from app.service.events import EscalationRequested, TicketEvent
from app.service.model import (
    Answer,
    NewTicket,
    QueueCursor,
    Ticket,
    TicketStatus,
    TicketSummary,
)
from app.service.ticket_errors import PublishFailed

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TTL_SECONDS = 72 * 3600
HOUR = timedelta(hours=1)


def make_ticket(**overrides: Any) -> Ticket:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "escalation_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "update_id": uuid.uuid4(),
        "context": {"schema_version": 1, "species": "Basil"},
        "status": TicketStatus.OPEN,
        "created_at": NOW - HOUR,
        "expires_at": NOW + 71 * HOUR,
        "answered_at": None,
        "cancelled_at": None,
        "terminal_event_published_at": None,
        "answer": None,
    }
    return Ticket(**{**fields, **overrides})


def answered_ticket(expert_id: uuid.UUID | None = None, **overrides: Any) -> Ticket:
    answer = Answer(expert_id or uuid.uuid4(), "Water less.")
    fields = {"status": TicketStatus.ANSWERED, "answered_at": NOW - HOUR // 2, "answer": answer}
    return make_ticket(**{**fields, **overrides})


def cancelled_ticket(**overrides: Any) -> Ticket:
    fields = {
        "status": TicketStatus.CANCELLED,
        "created_at": NOW - 80 * HOUR,
        "expires_at": NOW - 8 * HOUR,
        "cancelled_at": NOW - HOUR,
    }
    return make_ticket(**{**fields, **overrides})


def escalation_request(**overrides: Any) -> EscalationRequested:
    escalation_id = uuid.uuid4()
    fields: dict[str, Any] = {
        "event_id": escalation_id,
        "occurred_at": NOW - HOUR,
        "escalation_id": escalation_id,
        "user_id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "update_id": uuid.uuid4(),
        "accept_until": NOW + 23 * HOUR,
        "context": {"schema_version": 1, "species": "Basil", "user_question": "Why spots?"},
    }
    return EscalationRequested(**{**fields, **overrides})


def escalation_body(**overrides: Any) -> dict[str, Any]:
    """The `escalation.requested` message body as plant-care publishes it."""
    request = escalation_request()
    body = {
        "event_id": str(request.event_id),
        "occurred_at": request.occurred_at.isoformat(),
        "escalation_id": str(request.escalation_id),
        "user_id": str(request.user_id),
        "journal_id": str(request.journal_id),
        "update_id": str(request.update_id),
        "accept_until": request.accept_until.isoformat(),
        "context": request.context,
    }
    return {**body, **overrides}


class FakeTickets:
    """Mirrors the SQL repository's guards; `now` stands in for the database clock."""

    def __init__(self, log: list[str] | None = None) -> None:
        self.rows: dict[uuid.UUID, Ticket] = {}
        self.now = NOW
        self.log = log if log is not None else []
        self.create_error: Exception | None = None
        self.last_claim: tuple[Any, ...] | None = None

    def add(self, ticket: Ticket) -> Ticket:
        self.rows[ticket.id] = ticket
        return ticket

    def create(self, ticket: NewTicket, ttl_seconds: int, accept_until: datetime) -> bool:
        if self.create_error is not None:
            raise self.create_error
        if self.get_by_escalation(ticket.escalation_id) is not None or self.now >= accept_until:
            return False
        self.add(
            make_ticket(
                id=ticket.id,
                escalation_id=ticket.escalation_id,
                user_id=ticket.user_id,
                journal_id=ticket.journal_id,
                update_id=ticket.update_id,
                context=ticket.context,
                created_at=self.now,
                expires_at=self.now + timedelta(seconds=ttl_seconds),
            )
        )
        return True

    def get(self, ticket_id: uuid.UUID) -> Ticket | None:
        return self.rows.get(ticket_id)

    def get_by_escalation(self, escalation_id: uuid.UUID) -> Ticket | None:
        return next((t for t in self.rows.values() if t.escalation_id == escalation_id), None)

    def open_queue(self, limit: int, after: QueueCursor | None) -> list[TicketSummary]:
        open_tickets = sorted(
            (
                t
                for t in self.rows.values()
                if t.status is TicketStatus.OPEN and t.expires_at > self.now
            ),
            key=lambda t: (t.expires_at, t.id),
        )
        if after is not None:
            open_tickets = [
                t for t in open_tickets if (t.expires_at, t.id) > (after.expires_at, after.id)
            ]
        return [
            TicketSummary(t.id, t.context.get("species"), t.created_at, t.expires_at)
            for t in open_tickets[:limit]
        ]

    def answer(self, ticket_id: uuid.UUID, expert_id: uuid.UUID, body: str) -> bool:
        ticket = self.rows.get(ticket_id)
        if (
            ticket is None
            or ticket.status is not TicketStatus.OPEN
            or ticket.expires_at <= self.now
        ):
            return False
        self.rows[ticket_id] = replace(
            ticket,
            status=TicketStatus.ANSWERED,
            answered_at=self.now,
            answer=Answer(expert_id, body),
        )
        return True

    def claim_expired(self, limit: int) -> list[Ticket]:
        self.last_claim = ("expired", limit)
        due = sorted(
            (
                t
                for t in self.rows.values()
                if t.status is TicketStatus.OPEN and t.expires_at <= self.now
            ),
            key=lambda t: (t.expires_at, t.id),
        )[:limit]
        for ticket in due:
            self.rows[ticket.id] = replace(
                ticket, status=TicketStatus.CANCELLED, cancelled_at=self.now
            )
        return [self.rows[t.id] for t in due]

    def claim_unpublished(self, grace_seconds: float, limit: int) -> list[Ticket]:
        self.last_claim = ("unpublished", grace_seconds, limit)
        cutoff = self.now - timedelta(seconds=grace_seconds)
        due = [
            t
            for t in self.rows.values()
            if t.status is not TicketStatus.OPEN
            and t.terminal_event_published_at is None
            and (t.answered_at or t.cancelled_at) <= cutoff
        ]
        return sorted(due, key=lambda t: t.id)[:limit]

    def mark_published(self, ticket_id: uuid.UUID) -> bool:
        ticket = self.rows.get(ticket_id)
        if ticket is None or ticket.terminal_event_published_at is not None:
            return False
        self.rows[ticket_id] = replace(ticket, terminal_event_published_at=self.now)
        self.log.append("mark published")
        return True


class FakeUnitOfWork:
    def __init__(self, tickets: FakeTickets | None = None, log: list[str] | None = None) -> None:
        self.log = log if log is not None else []
        self.tickets = tickets or FakeTickets(self.log)
        self.commits = 0
        self.fail_commit_with: Exception | None = None

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def commit(self) -> None:
        if self.fail_commit_with is not None:
            raise self.fail_commit_with
        self.commits += 1
        self.log.append("commit")


class RecordingEvents:
    def __init__(self, log: list[str] | None = None) -> None:
        self.published: list[TicketEvent] = []
        self.correlation_ids: list[str | None] = []
        self.log = log if log is not None else []
        self.failing = False
        self.failing_ticket_ids: set[uuid.UUID] = set()

    def publish(self, event: TicketEvent) -> None:
        if self.failing or event.ticket_id in self.failing_ticket_ids:
            raise PublishFailed(f"{event.event_type} rejected")
        self.published.append(event)
        self.correlation_ids.append(get_correlation_id())
        self.log.append(f"publish {event.event_type}")

    @property
    def types(self) -> list[str]:
        return [event.event_type for event in self.published]
