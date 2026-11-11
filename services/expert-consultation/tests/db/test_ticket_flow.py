import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AnswerModel, TicketModel
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.maintenance import TicketMaintenance
from app.service.ticket_errors import TicketAlreadyAnswered, TicketExpired
from app.service.ticket_publisher import TerminalEventPublisher
from app.service.tickets import TicketService
from tests.db.factories import add_answered_ticket, add_overdue_ticket, add_ticket
from tests.unit.fakes import RecordingEvents, escalation_request

TTL_SECONDS = 72 * 3600
GRACE_SECONDS = 30.0
UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class Flow:
    def __init__(self, engine: Engine, unit_of_work: UnitOfWorkFactory) -> None:
        self.engine = engine
        self.events = RecordingEvents()
        self.terminal_events = TerminalEventPublisher(unit_of_work, self.events)
        self.service = TicketService(unit_of_work, self.events, self.terminal_events, TTL_SECONDS)
        self.unit_of_work = unit_of_work
        self.maintenance = self._maintenance(GRACE_SECONDS)

    def maintenance_without_grace(self) -> TicketMaintenance:
        return self._maintenance(0)

    def _maintenance(self, grace: float) -> TicketMaintenance:
        return TicketMaintenance(self.unit_of_work, self.events, self.terminal_events, grace, 10)

    def seed(self, add, *args, **fields):
        with Session(self.engine) as session:
            row = add(session, *args, **fields)
            session.commit()
        return row

    def tickets(self) -> list[TicketModel]:
        with Session(self.engine) as session:
            return list(session.scalars(select(TicketModel)))

    def count_answers(self) -> int:
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(AnswerModel))

    def forget_publication(self, ticket_id: uuid.UUID) -> None:
        with Session(self.engine) as session:
            session.execute(
                update(TicketModel)
                .where(TicketModel.id == ticket_id)
                .values(terminal_event_published_at=None)
            )
            session.commit()


@pytest.fixture
def flow(engine: Engine, unit_of_work: UnitOfWorkFactory) -> Flow:
    return Flow(engine, unit_of_work)


def in_an_hour() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def test_an_escalation_becomes_one_ticket_even_when_delivered_twice(flow: Flow):
    request = escalation_request(accept_until=in_an_hour())

    flow.service.create_from_escalation(request)
    flow.service.create_from_escalation(request)

    (ticket,) = flow.tickets()
    assert ticket.status == "open"
    assert ticket.expires_at - ticket.created_at == timedelta(seconds=TTL_SECONDS)
    assert ticket.context == request.context
    assert len(flow.events.published) == 2
    assert flow.events.published[0] == flow.events.published[1]
    assert flow.events.published[0].ticket_id == ticket.id


def test_an_escalation_past_its_window_creates_nothing(flow: Flow):
    flow.service.create_from_escalation(
        escalation_request(accept_until=datetime.now(UTC) - timedelta(seconds=1))
    )

    assert flow.tickets() == []
    assert flow.events.published == []


def test_a_redelivery_after_the_window_still_republishes_for_an_accepted_ticket(flow: Flow):
    ticket = flow.seed(add_ticket)
    request = escalation_request(
        escalation_id=ticket["escalation_id"],
        accept_until=datetime.now(UTC) - timedelta(hours=1),
    )

    flow.service.create_from_escalation(request)

    assert [event.ticket_id for event in flow.events.published] == [ticket["id"]]
    assert len(flow.tickets()) == 1


def test_a_redelivery_of_an_answered_ticket_also_sends_the_answer_and_records_it(flow: Flow):
    ticket, answer = flow.seed(add_answered_ticket)

    flow.service.create_from_escalation(
        escalation_request(escalation_id=ticket["escalation_id"], accept_until=in_an_hour())
    )

    assert flow.events.types == ["ticket.created", "ticket.answered"]
    assert flow.events.published[1].answer_text == answer["body"]
    (stored,) = flow.tickets()
    assert stored.terminal_event_published_at is not None


def test_a_second_open_ticket_for_an_update_breaks_the_invariant_and_publishes_nothing(
    flow: Flow,
):
    first = flow.seed(add_ticket)

    with pytest.raises(IntegrityError) as raised:
        flow.service.create_from_escalation(
            escalation_request(update_id=first["update_id"], accept_until=in_an_hour())
        )

    assert raised.value.orig.diag.constraint_name == "uq_tickets_active_update"
    assert flow.events.published == []
    assert len(flow.tickets()) == 1


def test_an_answer_is_stored_published_and_recorded(flow: Flow):
    ticket = flow.seed(add_ticket)
    expert = uuid.uuid4()

    result = flow.service.answer(ticket["id"], expert, "Water less.")

    (stored,) = flow.tickets()
    assert result.created is True
    assert (stored.status, stored.answered_at is not None) == ("answered", True)
    assert stored.terminal_event_published_at is not None
    assert flow.count_answers() == 1
    (event,) = flow.events.published
    assert (event.ticket_id, event.answer_text, event.answered_at) == (
        ticket["id"],
        "Water less.",
        stored.answered_at,
    )


def test_the_same_expert_repeating_the_answer_gets_a_200_style_replay(flow: Flow):
    ticket = flow.seed(add_ticket)
    expert = uuid.uuid4()
    flow.service.answer(ticket["id"], expert, "Water less.")

    repeated = flow.service.answer(ticket["id"], expert, "Water less.")

    assert repeated.created is False
    assert flow.count_answers() == 1
    assert flow.events.published[0] == flow.events.published[1]


def test_another_expert_is_refused_after_an_answer(flow: Flow):
    ticket = flow.seed(add_ticket)
    flow.service.answer(ticket["id"], uuid.uuid4(), "Water less.")

    with pytest.raises(TicketAlreadyAnswered):
        flow.service.answer(ticket["id"], uuid.uuid4(), "Repot it.")

    assert flow.count_answers() == 1


def test_an_overdue_ticket_that_the_sweep_has_not_reached_cannot_be_answered(flow: Flow):
    ticket = flow.seed(add_overdue_ticket)

    with pytest.raises(TicketExpired):
        flow.service.answer(ticket["id"], uuid.uuid4(), "Too late.")

    assert flow.count_answers() == 0
    assert flow.events.published == []


def test_the_sweep_cancels_an_overdue_ticket_and_announces_it_once(flow: Flow):
    ticket = flow.seed(add_overdue_ticket)

    assert flow.maintenance.expire_overdue() == 1
    assert flow.maintenance.expire_overdue() == 0

    (stored,) = flow.tickets()
    assert stored.status == "cancelled"
    assert stored.cancelled_at >= stored.expires_at
    assert stored.terminal_event_published_at is not None
    assert [(e.event_type, e.ticket_id) for e in flow.events.published] == [
        ("ticket.cancelled", ticket["id"])
    ]


def test_a_cancelled_ticket_refuses_an_answer(flow: Flow):
    ticket = flow.seed(add_overdue_ticket)
    flow.maintenance.expire_overdue()

    with pytest.raises(TicketExpired):
        flow.service.answer(ticket["id"], uuid.uuid4(), "Too late.")


def test_the_republisher_resends_an_unrecorded_answer_identically(flow: Flow):
    ticket = flow.seed(add_ticket)
    flow.service.answer(ticket["id"], uuid.uuid4(), "Water less.")
    original = flow.events.published[0]
    flow.forget_publication(ticket["id"])

    assert flow.maintenance_without_grace().republish_terminal() == 1

    assert flow.events.published[1] == original
    (stored,) = flow.tickets()
    assert stored.terminal_event_published_at is not None


def test_a_recorded_event_is_not_resent(flow: Flow):
    flow.seed(add_overdue_ticket)
    flow.maintenance.expire_overdue()
    sent_so_far = len(flow.events.published)

    assert flow.maintenance_without_grace().republish_terminal() == 0

    assert len(flow.events.published) == sent_so_far


def test_a_ticket_still_inside_the_grace_is_left_to_its_own_publisher(flow: Flow):
    ticket = flow.seed(add_ticket)
    flow.service.answer(ticket["id"], uuid.uuid4(), "Water less.")
    flow.forget_publication(ticket["id"])

    assert flow.maintenance.republish_terminal() == 0
