import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from app.repository.tickets import SqlAlchemyTicketRepository, to_ticket
from app.service.model import NewTicket, QueueCursor, TicketStatus

DIALECT = postgresql.dialect()
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class Result:
    def __init__(self, scalar=None, rows=()) -> None:
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class RecordingSession:
    """Records the statements; each call answers with the next canned result."""

    def __init__(self, *results: Result) -> None:
        self.results = list(results)
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0) if self.results else Result()

    def sql(self, index: int = 0) -> str:
        return " ".join(str(self.statements[index].compile(dialect=DIALECT)).split())


def new_ticket() -> NewTicket:
    ids = [uuid.uuid4() for _ in range(5)]
    return NewTicket(*ids, context={"schema_version": 1})


def test_create_inserts_inside_the_acceptance_window_and_ignores_a_repeated_escalation():
    session = RecordingSession(Result(scalar=uuid.uuid4()))

    created = SqlAlchemyTicketRepository(session).create(new_ticket(), 259200, NOW)

    sql = session.sql()
    assert created is True
    assert sql.startswith(
        "INSERT INTO tickets (id, escalation_id, user_id, journal_id, update_id, context,"
        " expires_at) SELECT"
    )
    assert "WHERE now() < " in sql
    assert "now() + make_interval(0, 0, 0, 0, 0, 0, " in sql
    assert sql.endswith("ON CONFLICT (escalation_id) DO NOTHING RETURNING tickets.id")


def test_create_reports_false_when_nothing_was_inserted():
    assert (
        SqlAlchemyTicketRepository(RecordingSession(Result())).create(new_ticket(), 60, NOW)
        is False
    )


def test_the_expiry_claim_is_one_statement_that_skips_locked_rows_and_cancels_by_the_clock():
    session = RecordingSession()

    assert SqlAlchemyTicketRepository(session).claim_expired(10) == []

    sql = session.sql()
    assert sql.startswith(
        "WITH due AS (SELECT tickets.id AS id FROM tickets WHERE tickets.status ="
    )
    assert "tickets.expires_at <= now() ORDER BY tickets.expires_at, tickets.id" in sql
    assert "FOR UPDATE SKIP LOCKED) UPDATE tickets SET status=%(status)s" in sql
    assert "cancelled_at=now() FROM due WHERE tickets.id = due.id RETURNING" in sql


def test_the_republisher_claim_locks_only_tickets_and_skips_locked_rows():
    session = RecordingSession()

    assert SqlAlchemyTicketRepository(session).claim_unpublished(30.0, 10) == []

    sql = session.sql()
    assert "LEFT OUTER JOIN answers ON answers.ticket_id = tickets.id" in sql
    assert "tickets.terminal_event_published_at IS NULL" in sql
    assert "coalesce(tickets.answered_at, tickets.cancelled_at) <= now() - make_interval(" in sql
    assert "ORDER BY tickets.id LIMIT" in sql
    assert sql.endswith("FOR UPDATE OF tickets SKIP LOCKED")


def test_the_queue_pages_by_deadline_then_id():
    session = RecordingSession()
    cursor = QueueCursor(NOW, uuid.uuid4())

    SqlAlchemyTicketRepository(session).open_queue(5, cursor)

    sql = session.sql()
    assert "tickets.expires_at > now()" in sql
    assert "(tickets.expires_at, tickets.id) > (" in sql
    assert "ORDER BY tickets.expires_at, tickets.id LIMIT" in sql


def test_the_first_queue_page_has_no_cursor_condition():
    session = RecordingSession()

    SqlAlchemyTicketRepository(session).open_queue(5, None)

    assert "(tickets.expires_at, tickets.id) >" not in session.sql()


def test_an_answer_is_guarded_by_the_status_and_the_deadline_and_then_stored():
    session = RecordingSession(Result(scalar=uuid.uuid4()))

    answered = SqlAlchemyTicketRepository(session).answer(uuid.uuid4(), uuid.uuid4(), "Water less")

    assert answered is True
    assert "tickets.status = " in session.sql(0)
    assert "tickets.expires_at > now()" in session.sql(0)
    assert session.sql(1).startswith("INSERT INTO answers (ticket_id, expert_id, body)")


def test_a_refused_answer_writes_no_answer_row():
    session = RecordingSession(Result())

    assert not SqlAlchemyTicketRepository(session).answer(uuid.uuid4(), uuid.uuid4(), "x")
    assert len(session.statements) == 1


def test_marking_published_is_guarded_by_the_empty_flag():
    session = RecordingSession(Result(scalar=uuid.uuid4()))

    assert SqlAlchemyTicketRepository(session).mark_published(uuid.uuid4())

    assert "terminal_event_published_at=now()" in session.sql()
    assert "tickets.terminal_event_published_at IS NULL" in session.sql()


def test_a_row_without_an_answer_maps_to_a_ticket_without_one():
    row = {
        "id": uuid.uuid4(),
        "escalation_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "update_id": uuid.uuid4(),
        "context": {},
        "status": "cancelled",
        "created_at": NOW,
        "expires_at": NOW,
        "answered_at": None,
        "cancelled_at": NOW,
        "terminal_event_published_at": None,
    }

    ticket = to_ticket(row)

    assert ticket.status is TicketStatus.CANCELLED
    assert ticket.answer is None


def test_a_joined_answer_is_part_of_the_ticket():
    expert = uuid.uuid4()
    row = {
        "id": uuid.uuid4(),
        "escalation_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "update_id": uuid.uuid4(),
        "context": {},
        "status": "answered",
        "created_at": NOW,
        "expires_at": NOW,
        "answered_at": NOW,
        "cancelled_at": None,
        "terminal_event_published_at": None,
        "answer_expert_id": expert,
        "answer_body": "Water less",
    }

    answer = to_ticket(row).answer

    assert (answer.expert_id, answer.body) == (expert, "Water less")
