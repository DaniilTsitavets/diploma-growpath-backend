import uuid
from dataclasses import asdict, replace

import pytest

from app.service.events import (
    NS_GROWPATH_EVENTS,
    TicketAnswered,
    TicketCancelled,
    TicketCreated,
    created_event,
    event_id,
    terminal_event,
)
from app.service.model import TicketStatus
from tests.unit.fakes import answered_ticket, cancelled_ticket, make_ticket


def test_the_namespace_is_the_documented_constant():
    assert NS_GROWPATH_EVENTS == uuid.UUID("6f1b2c4e-8a3d-4e5f-9b7a-1c2d3e4f5a6b")


def test_the_event_id_is_a_version_5_uuid_of_the_ticket_and_the_type():
    ticket_id = uuid.UUID("0b8f4d0e-6a3c-4c0a-9c55-2f6a1d9a7e11")

    assert event_id(ticket_id, "ticket.created") == uuid.uuid5(
        NS_GROWPATH_EVENTS, "0b8f4d0e-6a3c-4c0a-9c55-2f6a1d9a7e11:ticket.created"
    )
    assert event_id(ticket_id, "ticket.created").version == 5


def test_the_event_id_differs_per_type_and_per_ticket_but_never_per_call():
    first, second = uuid.uuid4(), uuid.uuid4()

    ids = {event_id(first, t) for t in ("ticket.created", "ticket.answered", "ticket.cancelled")}

    assert len(ids) == 3
    assert event_id(first, "ticket.created") == event_id(first, "ticket.created")
    assert event_id(first, "ticket.created") != event_id(second, "ticket.created")


def test_the_created_event_is_built_from_the_row():
    ticket = make_ticket()

    event = created_event(ticket)

    assert event == TicketCreated(
        event_id=event_id(ticket.id, "ticket.created"),
        occurred_at=ticket.created_at,
        ticket_id=ticket.id,
        escalation_id=ticket.escalation_id,
        user_id=ticket.user_id,
    )


def test_the_answered_event_carries_the_answer_and_takes_its_time_from_the_row():
    ticket = answered_ticket()

    event = terminal_event(ticket)

    assert event == TicketAnswered(
        event_id=event_id(ticket.id, "ticket.answered"),
        occurred_at=ticket.answered_at,
        ticket_id=ticket.id,
        escalation_id=ticket.escalation_id,
        user_id=ticket.user_id,
        journal_id=ticket.journal_id,
        answer_text="Water less.",
        answered_at=ticket.answered_at,
    )


def test_the_cancelled_event_takes_its_time_from_the_row():
    ticket = cancelled_ticket()

    event = terminal_event(ticket)

    assert event == TicketCancelled(
        event_id=event_id(ticket.id, "ticket.cancelled"),
        occurred_at=ticket.cancelled_at,
        ticket_id=ticket.id,
        escalation_id=ticket.escalation_id,
        user_id=ticket.user_id,
    )


@pytest.mark.parametrize("build", [created_event, terminal_event], ids=["created", "terminal"])
def test_an_event_rebuilt_later_is_identical_to_the_first(build):
    ticket = answered_ticket() if build is terminal_event else make_ticket()
    later_read = replace(ticket, terminal_event_published_at=ticket.created_at)

    assert asdict(build(later_read)) == asdict(build(ticket))


@pytest.mark.parametrize(
    "ticket",
    [
        make_ticket(),
        make_ticket(status=TicketStatus.ANSWERED, answered_at=None),
        answered_ticket(answer=None),
        make_ticket(status=TicketStatus.CANCELLED, cancelled_at=None),
    ],
    ids=[
        "open",
        "answered without a time",
        "answered without an answer",
        "cancelled without a time",
    ],
)
def test_a_ticket_without_a_terminal_state_has_no_terminal_event(ticket):
    with pytest.raises(ValueError, match="no terminal event"):
        terminal_event(ticket)


def test_the_event_types_are_the_published_names():
    assert (TicketCreated.event_type, TicketAnswered.event_type, TicketCancelled.event_type) == (
        "ticket.created",
        "ticket.answered",
        "ticket.cancelled",
    )
