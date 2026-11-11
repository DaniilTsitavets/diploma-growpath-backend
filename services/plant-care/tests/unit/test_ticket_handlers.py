import itertools
import uuid
from datetime import UTC, datetime

import pytest

from app.service.model import Escalation, EscalationStatus
from app.service.ticket_events import TicketAnswered, TicketCancelled, TicketCreated
from app.service.ticket_handlers import TicketEventHandlers
from tests.unit.ticket_fakes import TicketUnitOfWork

ANSWERED_AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
TICKET = uuid.uuid4()


class Setup:
    def __init__(self) -> None:
        self.uow = TicketUnitOfWork()
        journal = self.uow.journals.seed(uuid.uuid4())
        update = self.uow.updates.seed(journal.id)
        self.escalation = self.uow.escalations.seed(update.id)
        self.handlers = TicketEventHandlers(lambda: self.uow)

    @property
    def row(self) -> Escalation:
        return self.uow.escalations.rows[self.escalation.id]

    def created(self, ticket_id: uuid.UUID = TICKET) -> None:
        self.handlers.ticket_created(TicketCreated(self.escalation.id, ticket_id))

    def answered(self, ticket_id: uuid.UUID = TICKET, text: str = "Repot it.") -> None:
        self.handlers.ticket_answered(
            TicketAnswered(self.escalation.id, ticket_id, text, ANSWERED_AT)
        )

    def cancelled(self, ticket_id: uuid.UUID = TICKET) -> None:
        self.handlers.ticket_cancelled(TicketCancelled(self.escalation.id, ticket_id))


@pytest.fixture
def setup() -> Setup:
    return Setup()


def test_ticket_created_opens_a_requested_escalation(setup: Setup):
    setup.created()

    assert setup.row.status is EscalationStatus.TICKET_OPEN
    assert setup.row.ticket_id == TICKET
    assert setup.uow.commits == 1


def test_ticket_answered_stores_the_answer_after_the_ticket_was_opened(setup: Setup):
    setup.created()
    setup.answered()

    assert setup.row.status is EscalationStatus.ANSWERED
    assert (setup.row.answer_text, setup.row.answered_at) == ("Repot it.", ANSWERED_AT)
    assert setup.row.ticket_id == TICKET


def test_ticket_answered_before_ticket_created_still_lands_with_the_ticket_id(setup: Setup):
    setup.answered()

    assert setup.row.status is EscalationStatus.ANSWERED
    assert setup.row.ticket_id == TICKET


def test_ticket_cancelled_cancels_a_requested_or_open_escalation(setup: Setup):
    setup.cancelled()

    assert setup.row.status is EscalationStatus.CANCELLED
    assert setup.row.ticket_id == TICKET
    assert setup.uow.escalations.get_active(setup.escalation.update_id) is None


def test_ticket_cancelled_cancels_an_open_escalation(setup: Setup):
    setup.created()
    setup.cancelled()

    assert setup.row.status is EscalationStatus.CANCELLED


@pytest.mark.parametrize("event", ["created", "answered", "cancelled"])
def test_a_repeated_event_changes_nothing_and_is_acknowledged(setup: Setup, event: str):
    getattr(setup, event)()
    after_first = setup.row

    getattr(setup, event)()

    assert setup.row == after_first
    assert setup.uow.commits == 2


def test_a_late_ticket_created_after_the_answer_is_ignored(setup: Setup):
    setup.answered()
    answered = setup.row

    setup.created()

    assert setup.row == answered


def test_a_late_cancel_does_not_undo_an_answer(setup: Setup):
    setup.answered()
    answered = setup.row

    setup.cancelled()

    assert setup.row == answered


def test_a_late_answer_does_not_revive_a_cancelled_escalation(setup: Setup):
    setup.cancelled()
    cancelled = setup.row

    setup.answered()

    assert setup.row == cancelled
    assert setup.row.answer_text is None


def test_a_late_ticket_created_after_the_cancel_is_ignored(setup: Setup):
    setup.cancelled()
    setup.created()

    assert setup.row.status is EscalationStatus.CANCELLED


def test_an_event_of_another_ticket_does_not_touch_an_escalation_that_has_one(setup: Setup):
    setup.created()
    other = uuid.uuid4()

    setup.answered(other)
    setup.cancelled(other)
    setup.created(other)

    assert setup.row.status is EscalationStatus.TICKET_OPEN
    assert setup.row.ticket_id == TICKET


def test_an_event_for_an_unknown_escalation_is_acknowledged(setup: Setup):
    setup.handlers.ticket_created(TicketCreated(uuid.uuid4(), TICKET))

    assert setup.uow.commits == 1
    assert setup.row.status is EscalationStatus.REQUESTED


@pytest.mark.parametrize(
    "order", list(itertools.permutations(["created", "answered", "cancelled"]))
)
def test_every_arrival_order_ends_in_a_state_the_database_checks_accept(order: tuple[str, ...]):
    setup = Setup()

    for event in order:
        getattr(setup, event)()

    row = setup.row
    assert row.ticket_id == TICKET
    assert row.status in (EscalationStatus.ANSWERED, EscalationStatus.CANCELLED)
    assert (row.status is EscalationStatus.ANSWERED) == (
        row.answer_text is not None and row.answered_at is not None
    )


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (("created", "answered", "cancelled"), EscalationStatus.ANSWERED),
        (("answered", "created", "cancelled"), EscalationStatus.ANSWERED),
        (("answered", "cancelled", "created"), EscalationStatus.ANSWERED),
        (("created", "cancelled", "answered"), EscalationStatus.CANCELLED),
        (("cancelled", "created", "answered"), EscalationStatus.CANCELLED),
        (("cancelled", "answered", "created"), EscalationStatus.CANCELLED),
    ],
)
def test_the_first_terminal_event_wins_whatever_the_arrival_order(
    order: tuple[str, ...], expected: EscalationStatus
):
    setup = Setup()

    for event in order:
        getattr(setup, event)()

    assert setup.row.status is expected


def test_every_order_with_duplicates_converges(setup: Setup):
    for event in ["answered", "created", "answered", "created", "cancelled", "answered"]:
        getattr(setup, event)()

    assert setup.row.status is EscalationStatus.ANSWERED
    assert setup.row.answer_text == "Repot it."


def test_a_failing_commit_propagates_so_the_message_is_not_acknowledged(setup: Setup):
    setup.uow.fail_commit_with = RuntimeError("db down")

    with pytest.raises(RuntimeError):
        setup.created()

    assert setup.row.status is EscalationStatus.REQUESTED


def test_applied_and_ignored_events_are_logged_differently(
    setup: Setup, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level("INFO"):
        setup.created()
        setup.created()

    assert [r.message for r in caplog.records] == [
        "ticket.created applied",
        "ticket.created duplicate or late, ignored",
    ]
    assert caplog.records[0].escalation_id == str(setup.escalation.id)
