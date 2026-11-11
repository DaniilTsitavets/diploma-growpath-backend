import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AnswerModel, TicketModel
from tests.db.factories import (
    add_answered_ticket,
    add_cancelled_ticket,
    add_overdue_ticket,
    add_ticket,
    db_now,
)
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import RecordingEvents
from tests.unit.settings import make_settings

EXPERT = uuid.uuid4()
AS_EXPERT = {"X-User-Id": str(EXPERT), "X-User-Groups": "experts"}


class Api:
    def __init__(self, database_url: URL, engine: Engine) -> None:
        self.engine = engine
        self.events = RecordingEvents()
        settings = make_settings(
            database_url=database_url.render_as_string(hide_password=False),
            queue_default_page_size=2,
        )
        app = create_app(settings, FakeQueue(), self.events, FakePhotoStorage())
        self.client = TestClient(app, raise_server_exceptions=False)

    def seed(self, add, *args, **fields):
        with Session(self.engine) as session:
            row = add(session, *args, **fields)
            session.commit()
        return row

    def ticket(self, ticket_id: uuid.UUID) -> TicketModel:
        with Session(self.engine) as session:
            return session.get(TicketModel, ticket_id)

    def answers(self) -> int:
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(AnswerModel))

    def answer(self, ticket_id, body="Water less often.", headers=AS_EXPERT):
        return self.client.post(
            f"/v1/expert/tickets/{ticket_id}/answer", json={"body": body}, headers=headers
        )


@pytest.fixture
def api(database_url: URL, engine: Engine) -> Api:
    return Api(database_url, engine)


def test_the_queue_lists_open_tickets_by_deadline_and_pages_with_a_cursor(api: Api):
    with Session(api.engine) as session:
        now = db_now(session)
    deadlines = [now + timedelta(hours=hours) for hours in (30, 10, 20)]
    tickets = [api.seed(add_ticket, expires_at=deadline)["id"] for deadline in deadlines]
    api.seed(add_overdue_ticket)
    api.seed(add_answered_ticket)
    api.seed(add_cancelled_ticket)

    first = api.client.get("/v1/expert/tickets", headers=AS_EXPERT).json()
    second = api.client.get(
        "/v1/expert/tickets", params={"after": first["next_cursor"]}, headers=AS_EXPERT
    ).json()

    assert [item["id"] for item in first["items"]] == [str(tickets[1]), str(tickets[2])]
    assert [item["id"] for item in second["items"]] == [str(tickets[0])]
    assert second["next_cursor"] is None


def test_a_ticket_is_shown_with_its_context_and_signed_photo_urls(api: Api):
    context = {
        "schema_version": 1,
        "species": "Мята",
        "photos": [{"kind": "soil", "storage_key": "k"}],
    }
    ticket = api.seed(add_ticket, context=context)

    response = api.client.get(f"/v1/expert/tickets/{ticket['id']}", headers=AS_EXPERT)

    body = response.json()
    assert response.status_code == 200
    assert body["context"] == context
    assert body["photos"] == [{"kind": "soil", "url": "http://storage.test/k?method=get"}]


def test_an_answer_is_stored_and_the_event_goes_out(api: Api):
    ticket = api.seed(add_ticket)

    response = api.answer(ticket["id"])

    stored = api.ticket(ticket["id"])
    assert response.status_code == 201
    assert (stored.status, stored.terminal_event_published_at is not None) == ("answered", True)
    assert api.answers() == 1
    (event,) = api.events.published
    assert (event.ticket_id, event.answer_text) == (ticket["id"], "Water less often.")


def test_repeating_the_answer_is_a_200_and_another_expert_a_409(api: Api):
    ticket = api.seed(add_ticket)
    api.answer(ticket["id"])

    repeated = api.answer(ticket["id"])
    other = api.answer(
        ticket["id"], headers={"X-User-Id": str(uuid.uuid4()), "X-User-Groups": "experts"}
    )

    assert repeated.status_code == 200
    assert (other.status_code, other.json()["code"]) == (409, "ticket-already-answered")
    assert api.answers() == 1
    assert len(api.events.published) == 2


def test_an_overdue_ticket_is_a_409_until_the_sweep_and_after_it(api: Api):
    ticket = api.seed(add_overdue_ticket)

    response = api.answer(ticket["id"])

    assert (response.status_code, response.json()["code"]) == (409, "ticket-expired")
    assert api.answers() == 0
    assert api.ticket(ticket["id"]).status == "open"


def test_answering_an_unknown_ticket_is_404(api: Api):
    assert api.answer(uuid.uuid4()).status_code == 404


def test_a_blank_answer_never_reaches_the_database(api: Api):
    ticket = api.seed(add_ticket)

    assert api.answer(ticket["id"], body=" \t\r\n ").status_code == 422
    assert api.answers() == 0
