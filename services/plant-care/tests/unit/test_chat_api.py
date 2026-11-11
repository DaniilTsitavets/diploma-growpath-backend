import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from growpath_observability import CORRELATION_HEADER, CorrelationIdMiddleware

from app.api import chat
from app.api.errors import register_error_handlers
from app.service.diagnosis_model import InvalidModelAnswer, ModelUnavailable
from tests.unit.chat_fakes import ChatScenario, FakeChatModel, answered
from tests.unit.scenario import USER

PROBLEM_JSON = "application/problem+json"
AS_USER = {"X-User-Id": str(USER)}


@pytest.fixture
def scenario() -> ChatScenario:
    return ChatScenario()


def client_for(scenario: ChatScenario, model: FakeChatModel | None = None) -> TestClient:
    app = FastAPI()
    app.state.chat_service = scenario.service(model or FakeChatModel(answered("S1")))
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app, 7)
    app.include_router(chat.router)
    return TestClient(app, raise_server_exceptions=False)


def turns_url(scenario: ChatScenario) -> str:
    return f"/v1/journals/{scenario.journal.id}/chat/turns"


def body(question: str = "How often to water?", turn_id: uuid.UUID | None = None) -> dict:
    return {"id": str(turn_id or uuid.uuid4()), "question": question}


def test_a_new_turn_is_201_with_the_answer_and_its_citations(scenario: ChatScenario):
    client = client_for(scenario)
    request = body()

    response = client.post(turns_url(scenario), json=request, headers=AS_USER)

    turn = response.json()
    assert response.status_code == 201
    assert turn["id"] == request["id"] and turn["journal_id"] == str(scenario.journal.id)
    assert turn["question"] == "How often to water?"
    assert (turn["outcome"], turn["answer"]) == ("answered", "Water when dry.")
    assert turn["model_effort"] == "high" and turn["created_at"] is not None
    assert turn["sources"] == [
        {
            "rank": 1,
            "section_id": str(scenario.sections[0].id),
            "document_title": "Document 1",
            "section_heading": "Heading 1",
            "source_url": "https://example.test/1",
        }
    ]
    assert turn["related"] == []
    assert "ai_reservation_id" not in turn


def test_a_repeated_id_is_200_with_the_stored_turn(scenario: ChatScenario):
    client = client_for(scenario)
    request = body()
    first = client.post(turns_url(scenario), json=request, headers=AS_USER)

    again = client.post(turns_url(scenario), json=request, headers=AS_USER)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()


@pytest.mark.parametrize("failure", [ModelUnavailable("down"), InvalidModelAnswer("garbage")])
def test_a_model_failure_is_200_with_the_sections_and_no_answer(
    scenario: ChatScenario, failure: Exception
):
    client = client_for(scenario, FakeChatModel(failure))
    request = body("Why are the leaves yellow?")

    response = client.post(turns_url(scenario), json=request, headers=AS_USER)

    turn = response.json()
    assert response.status_code == 200
    assert turn["outcome"] == "sources_only" and turn["answer"] is None
    assert turn["id"] == request["id"] and turn["question"] == "Why are the leaves yellow?"
    assert turn["created_at"] is None and turn["model_effort"] is None and turn["sources"] == []
    assert [section["section_heading"] for section in turn["related"]] == [
        "Heading 1",
        "Heading 2",
    ]
    assert turn["related"][0]["body"] == "Body of section 1."
    assert scenario.uow.chat_turns.turns == {}


def test_an_unavailable_quota_service_is_also_sources_only(scenario: ChatScenario):
    scenario.quota.unavailable = True

    response = client_for(scenario).post(turns_url(scenario), json=body(), headers=AS_USER)

    assert response.status_code == 200 and response.json()["outcome"] == "sources_only"


def test_the_same_id_may_be_retried_after_sources_only(scenario: ChatScenario):
    model = FakeChatModel(ModelUnavailable("down"), answered("S2"))
    client = client_for(scenario, model)
    request = body()

    first = client.post(turns_url(scenario), json=request, headers=AS_USER)
    second = client.post(turns_url(scenario), json=request, headers=AS_USER)

    assert first.json()["outcome"] == "sources_only"
    assert second.status_code == 201 and second.json()["outcome"] == "answered"


def test_an_exhausted_quota_is_409_with_its_code(scenario: ChatScenario):
    scenario.quota.exhausted = True

    response = client_for(scenario).post(turns_url(scenario), json=body(), headers=AS_USER)

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "ai-quota-exhausted"


def test_a_journal_of_another_user_is_404_for_both_endpoints(scenario: ChatScenario):
    foreign = scenario.uow.journals.seed(uuid.uuid4())
    client = client_for(scenario)
    url = f"/v1/journals/{foreign.id}/chat/turns"

    post = client.post(url, json=body(), headers=AS_USER)
    get = client.get(url, headers=AS_USER)

    assert (post.status_code, get.status_code) == (404, 404)
    assert post.headers["content-type"] == PROBLEM_JSON
    assert scenario.quota.reserved == []


def test_a_turn_id_from_another_journal_is_404(scenario: ChatScenario):
    other = scenario.uow.journals.seed(USER)
    taken = scenario.uow.chat_turns.seed(other.id)

    response = client_for(scenario).post(
        turns_url(scenario), json=body(turn_id=taken.id), headers=AS_USER
    )

    assert response.status_code == 404


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001, "bell\x07", "ta\tb"])
def test_an_invalid_question_is_422_and_the_quota_is_untouched(
    scenario: ChatScenario, question: str
):
    response = client_for(scenario).post(turns_url(scenario), json=body(question), headers=AS_USER)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert scenario.quota.reserved == []


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "Water?"},
        {"id": "not-a-uuid", "question": "Water?"},
        {"id": str(uuid.uuid4())},
        {"id": str(uuid.uuid4()), "question": "Water?", "extra": 1},
        {"id": str(uuid.uuid4()), "question": 5},
    ],
)
def test_a_body_that_does_not_match_the_contract_is_422(scenario: ChatScenario, payload: dict):
    response = client_for(scenario).post(turns_url(scenario), json=payload, headers=AS_USER)

    assert response.status_code == 422
    assert response.json()["errors"]


def test_the_user_header_is_required(scenario: ChatScenario):
    client = client_for(scenario)

    assert client.post(turns_url(scenario), json=body()).status_code == 401
    assert client.get(turns_url(scenario)).status_code == 401


def test_a_problem_carries_the_correlation_id(scenario: ChatScenario):
    scenario.quota.exhausted = True
    headers = {**AS_USER, CORRELATION_HEADER: "chat-corr-1"}

    response = client_for(scenario).post(turns_url(scenario), json=body(), headers=headers)

    assert response.json()["correlation_id"] == "chat-corr-1"
    assert response.headers[CORRELATION_HEADER] == "chat-corr-1"


def test_the_history_is_newest_first_and_paged_with_a_cursor(scenario: ChatScenario):
    for number in range(5):
        scenario.uow.chat_turns.seed(scenario.journal.id, f"q{number}", f"a{number}")
    client = client_for(scenario)

    first = client.get(turns_url(scenario), headers=AS_USER).json()
    second = client.get(
        turns_url(scenario), params={"before": first["next_cursor"]}, headers=AS_USER
    ).json()
    last = client.get(
        turns_url(scenario),
        params={"before": second["next_cursor"], "limit": 3},
        headers=AS_USER,
    ).json()

    assert [t["question"] for t in first["items"]] == ["q4", "q3"]
    assert [t["question"] for t in second["items"]] == ["q2", "q1"]
    assert [t["question"] for t in last["items"]] == ["q0"] and last["next_cursor"] is None
    assert first["items"][0]["related"] == [] and first["items"][0]["answer"] == "a4"


def test_the_history_of_an_empty_journal_is_empty(scenario: ChatScenario):
    response = client_for(scenario).get(turns_url(scenario), headers=AS_USER)

    assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": "x"}, {"before": ""}])
def test_bad_history_parameters_are_422(scenario: ChatScenario, params: dict):
    response = client_for(scenario).get(turns_url(scenario), params=params, headers=AS_USER)

    assert response.status_code == 422


def test_a_malformed_cursor_is_422(scenario: ChatScenario):
    response = client_for(scenario).get(
        turns_url(scenario), params={"before": "garbage"}, headers=AS_USER
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
