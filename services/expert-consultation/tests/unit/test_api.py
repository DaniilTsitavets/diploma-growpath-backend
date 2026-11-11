import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CORRELATION_HEADER, CorrelationIdMiddleware

from app.api import experts
from app.api.errors import register_error_handlers
from app.service.expert_tickets import ExpertTicketService
from app.service.ticket_publisher import TerminalEventPublisher
from app.service.tickets import TicketService
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import (
    HOUR,
    NOW,
    TTL_SECONDS,
    FakeUnitOfWork,
    RecordingEvents,
    answered_ticket,
    cancelled_ticket,
    make_ticket,
)

PROBLEM_JSON = "application/problem+json"
EXPERT = uuid.uuid4()
AS_EXPERT = {"X-User-Id": str(EXPERT), "X-User-Groups": "experts"}


class Harness:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.events = RecordingEvents(self.uow.log)
        self.storage = FakePhotoStorage()
        app = FastAPI()
        app.state.ticket_service = TicketService(
            lambda: self.uow,
            self.events,
            TerminalEventPublisher(lambda: self.uow, self.events),
            TTL_SECONDS,
        )
        app.state.expert_ticket_service = ExpertTicketService(lambda: self.uow, self.storage, 2, 3)
        app.add_middleware(CorrelationIdMiddleware)
        register_error_handlers(app, retry_after_seconds=5)
        app.include_router(experts.router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def add(self, ticket):
        return self.uow.tickets.add(ticket)


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def client(harness: Harness) -> TestClient:
    return harness.client


def answer(client: TestClient, ticket_id, body="Water less often.", headers=AS_EXPERT):
    return client.post(
        f"/v1/expert/tickets/{ticket_id}/answer", json={"body": body}, headers=headers
    )


@pytest.mark.parametrize(
    "path", ["/v1/expert/tickets", f"/v1/expert/tickets/{uuid.uuid4()}"], ids=["queue", "ticket"]
)
def test_the_user_header_is_required(client: TestClient, path: str):
    response = client.get(path, headers={"X-User-Groups": "experts"})

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON


def test_a_malformed_user_header_is_unauthorized(client: TestClient):
    headers = {"X-User-Id": "me", "X-User-Groups": "experts"}

    assert client.get("/v1/expert/tickets", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "groups", [None, "", "users", "expert", "supexperts", "users,admins"], ids=str
)
def test_a_caller_outside_the_experts_group_is_forbidden(client: TestClient, groups: str | None):
    headers = {"X-User-Id": str(EXPERT)}
    if groups is not None:
        headers["X-User-Groups"] = groups

    response = client.get("/v1/expert/tickets", headers=headers)

    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["status"] == 403


def test_the_experts_group_may_be_one_of_several(client: TestClient):
    headers = {"X-User-Id": str(EXPERT), "X-User-Groups": "users, experts"}

    assert client.get("/v1/expert/tickets", headers=headers).status_code == 200


@pytest.mark.parametrize(
    ("header", "allowed"),
    [
        ("experts", True),
        ("users,experts", True),
        ("[users, experts]", True),
        ("['users', 'experts']", True),
        ("[experts]", True),
        ("users", False),
        ("[users]", False),
        ("superexperts", False),
        ("", False),
    ],
)
def test_the_group_header_is_read_in_every_form_the_gateway_may_render(
    client: TestClient, header: str, allowed: bool
):
    headers = {"X-User-Id": str(EXPERT), "X-User-Groups": header}

    response = client.get("/v1/expert/tickets", headers=headers)

    assert response.status_code == (200 if allowed else 403)


def test_every_endpoint_checks_the_group_before_looking_at_the_ticket(client: TestClient):
    headers = {"X-User-Id": str(EXPERT), "X-User-Groups": "users"}

    assert client.get(f"/v1/expert/tickets/{uuid.uuid4()}", headers=headers).status_code == 403
    assert answer(client, uuid.uuid4(), headers=headers).status_code == 403


def test_the_queue_lists_open_tickets_with_a_cursor(harness: Harness, client: TestClient):
    tickets = [harness.add(make_ticket(expires_at=NOW + hours * HOUR)) for hours in (1, 2, 3)]
    harness.add(cancelled_ticket())

    first = client.get("/v1/expert/tickets", headers=AS_EXPERT).json()
    second = client.get(
        "/v1/expert/tickets", params={"after": first["next_cursor"]}, headers=AS_EXPERT
    ).json()

    assert [item["id"] for item in first["items"]] == [str(t.id) for t in tickets[:2]]
    assert first["items"][0]["species"] == "Basil"
    assert set(first["items"][0]) == {"id", "species", "created_at", "expires_at"}
    assert [item["id"] for item in second["items"]] == [str(tickets[2].id)]
    assert second["next_cursor"] is None


def test_the_queue_limit_is_honoured(harness: Harness, client: TestClient):
    for hours in (1, 2, 3):
        harness.add(make_ticket(expires_at=NOW + hours * HOUR))

    body = client.get("/v1/expert/tickets", params={"limit": 1}, headers=AS_EXPERT).json()

    assert len(body["items"]) == 1
    assert body["next_cursor"] is not None


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": "x"}, {"after": ""}, {"after": "garbage"}]
)
def test_a_bad_queue_query_is_422_problem_json(client: TestClient, params: dict):
    response = client.get("/v1/expert/tickets", params=params, headers=AS_EXPERT)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON


def test_a_ticket_is_shown_with_its_context_and_presigned_photo_urls(
    harness: Harness, client: TestClient
):
    context = {
        "schema_version": 1,
        "species": "Basil",
        "photos": [{"kind": "overview", "storage_key": "journals/j/updates/u/overview"}],
    }
    ticket = harness.add(make_ticket(context=context))

    response = client.get(f"/v1/expert/tickets/{ticket.id}", headers=AS_EXPERT)

    body = response.json()
    assert response.status_code == 200
    assert body["id"] == str(ticket.id)
    assert body["status"] == "open"
    assert body["context"] == context
    assert body["photos"] == [
        {"kind": "overview", "url": "http://storage.test/journals/j/updates/u/overview?method=get"}
    ]
    assert body["answer_text"] is None
    assert "user_id" not in body


def test_an_answered_ticket_shows_its_answer(harness: Harness, client: TestClient):
    ticket = harness.add(answered_ticket())

    body = client.get(f"/v1/expert/tickets/{ticket.id}", headers=AS_EXPERT).json()

    assert (body["status"], body["answer_text"]) == ("answered", "Water less.")


def test_an_unknown_ticket_is_404_problem_json_with_the_correlation_id(client: TestClient):
    response = client.get(
        f"/v1/expert/tickets/{uuid.uuid4()}", headers={**AS_EXPERT, CORRELATION_HEADER: "corr-3"}
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["correlation_id"] == "corr-3"


def test_a_malformed_ticket_id_is_422(client: TestClient):
    assert client.get("/v1/expert/tickets/not-a-uuid", headers=AS_EXPERT).status_code == 422


def test_a_storage_outage_is_503_with_a_retry_hint(harness: Harness, client: TestClient):
    context = {"schema_version": 1, "photos": [{"kind": "overview", "storage_key": "k"}]}
    ticket = harness.add(make_ticket(context=context))
    harness.storage.unavailable = True

    response = client.get(f"/v1/expert/tickets/{ticket.id}", headers=AS_EXPERT)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.headers["content-type"] == PROBLEM_JSON


def test_an_answer_is_created_and_announced(harness: Harness, client: TestClient):
    ticket = harness.add(make_ticket())

    response = answer(client, ticket.id)

    body = response.json()
    assert response.status_code == 201
    assert (body["ticket_id"], body["status"], body["answer_text"]) == (
        str(ticket.id),
        "answered",
        "Water less often.",
    )
    assert harness.events.types == ["ticket.answered"]


def test_repeating_the_answer_as_the_same_expert_is_a_200_and_sends_the_event_again(
    harness: Harness, client: TestClient
):
    ticket = harness.add(make_ticket())
    answer(client, ticket.id)

    response = answer(client, ticket.id)

    assert response.status_code == 200
    assert response.json()["answer_text"] == "Water less often."
    assert harness.events.types == ["ticket.answered", "ticket.answered"]


def test_another_expert_gets_a_409_with_a_code(harness: Harness, client: TestClient):
    ticket = harness.add(make_ticket())
    answer(client, ticket.id)
    other = {"X-User-Id": str(uuid.uuid4()), "X-User-Groups": "experts"}

    response = answer(client, ticket.id, headers=other)

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "ticket-already-answered"


def test_an_expired_ticket_gets_a_409_with_a_code(harness: Harness, client: TestClient):
    ticket = harness.add(cancelled_ticket())

    response = answer(client, ticket.id)

    assert response.status_code == 409
    assert response.json()["code"] == "ticket-expired"


def test_answering_an_unknown_ticket_is_404(client: TestClient):
    assert answer(client, uuid.uuid4()).status_code == 404


@pytest.mark.parametrize("body", ["", " ", "\t", "\r\n", " \t\r\n  "])
def test_a_blank_answer_is_422(harness: Harness, client: TestClient, body: str):
    ticket = harness.add(make_ticket())

    response = answer(client, ticket.id, body=body)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert harness.events.published == []


def test_an_answer_may_have_exactly_five_thousand_characters_but_not_one_more(
    harness: Harness, client: TestClient
):
    first, second = harness.add(make_ticket()), harness.add(make_ticket())

    assert answer(client, first.id, body="я" * 5000).status_code == 201
    assert answer(client, second.id, body="я" * 5001).status_code == 422


def test_an_answer_with_unknown_fields_or_no_body_is_422(harness: Harness, client: TestClient):
    ticket = harness.add(make_ticket())
    url = f"/v1/expert/tickets/{ticket.id}/answer"

    assert client.post(url, json={"body": "x", "extra": 1}, headers=AS_EXPERT).status_code == 422
    assert client.post(url, json={}, headers=AS_EXPERT).status_code == 422
    assert client.post(url, headers=AS_EXPERT).status_code == 422
