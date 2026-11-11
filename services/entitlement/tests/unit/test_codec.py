import json
import uuid
from datetime import timedelta

import pytest

from app.messaging.codec import (
    decode_order_placed,
    decode_ticket_cancelled,
    decode_ticket_created,
)
from app.service.errors import InvalidMessage
from tests.unit.fakes import NOW, order_placed_body, ticket_body

USER, ESCALATION = uuid.uuid4(), uuid.uuid4()


def test_order_placed_takes_the_fields_the_projection_needs_from_the_full_contract():
    body = order_placed_body(
        user_id=str(USER),
        order_id=str(ESCALATION),
        total_paid_cents=1234,
        placed_at="2026-11-11T14:00:00+02:00",
    )

    event = decode_order_placed(json.dumps(body))

    assert (event.order_id, event.user_id, event.total_paid_cents) == (ESCALATION, USER, 1234)
    assert event.placed_at == NOW
    assert event.placed_at.utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize("decode", [decode_ticket_created, decode_ticket_cancelled])
def test_ticket_events_carry_the_escalation_and_the_user(decode):
    event = decode(json.dumps(ticket_body(ESCALATION, USER, answer_text="ignored")))

    assert (event.escalation_id, event.user_id) == (ESCALATION, USER)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "{",
        "[]",
        json.dumps({"order_id": str(ESCALATION), "user_id": str(USER)}),
        json.dumps(order_placed_body(total_paid_cents="many")),
        json.dumps(order_placed_body(user_id=None)),
    ],
)
def test_malformed_order_placed_is_invalid(body: str):
    with pytest.raises(InvalidMessage):
        decode_order_placed(body)


@pytest.mark.parametrize("decode", [decode_ticket_created, decode_ticket_cancelled])
@pytest.mark.parametrize(
    "body",
    [{}, {"escalation_id": str(ESCALATION)}, {"escalation_id": "x", "user_id": str(USER)}],
)
def test_malformed_ticket_event_is_invalid(decode, body: dict):
    with pytest.raises(InvalidMessage):
        decode(json.dumps(body))


def test_a_negative_amount_is_left_to_the_database_to_reject():
    event = decode_order_placed(json.dumps(order_placed_body(total_paid_cents=-1)))

    assert event.total_paid_cents == -1
