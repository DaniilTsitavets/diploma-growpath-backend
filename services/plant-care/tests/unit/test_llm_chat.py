import json

import httpx
import pytest

from app.llm.chat import LlmChatModel
from app.llm.chat_factory import create_chat_model
from app.llm.factory import create_diagnosis_model, create_model_guard, create_transport
from app.llm.guard import ModelGuard
from app.llm.mock_chat import MockChatTransport
from app.llm.openrouter import OpenRouterTransport
from app.llm.transport import ModelPrompt, TransientModelError
from app.resilience import CircuitBreaker, RetryPolicy
from app.service.chat_model import ChatOutcome, PriorTurn
from app.service.chat_prompt import build_chat_prompt
from app.service.diagnosis_model import Image, InvalidModelAnswer, ModelUnavailable
from app.service.model import ModelEffort
from tests.unit.chat_fakes import make_chat_request as make_request
from tests.unit.knowledge_fakes import make_section
from tests.unit.settings import make_settings

GOOD_REPLY = json.dumps({"outcome": "answered", "answer": "Water weekly.", "cited": ["S1"]})


class RecordingTransport:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[tuple[ModelPrompt, ModelEffort]] = []

    def complete(self, prompt: ModelPrompt, effort: ModelEffort) -> str:
        self.prompts.append((prompt, effort))
        return self.reply


class NoPhotos:
    def read(self, storage_key: str) -> Image:
        raise AssertionError("the chat reads no photos")


def guard(bulkhead: int = 2) -> ModelGuard:
    return ModelGuard(
        CircuitBreaker("model", 5, 60), RetryPolicy(2, 0, 0), bulkhead, sleep=lambda _: None
    )


def test_the_prompt_and_the_effort_go_to_the_transport_without_images():
    transport = RecordingTransport(GOOD_REPLY)

    answer = LlmChatModel(transport, guard(), 1000).answer(make_request(effort=ModelEffort.MEDIUM))

    ((prompt, effort),) = transport.prompts
    assert effort is ModelEffort.MEDIUM
    assert prompt.images == []
    assert '<kb_section id="S1"' in prompt.user and "<question>" in prompt.user
    assert (answer.outcome, answer.answer, answer.cited) == (
        ChatOutcome.ANSWERED,
        "Water weekly.",
        ["S1"],
    )


def test_the_history_answers_are_cut_to_the_configured_length():
    transport = RecordingTransport(GOOD_REPLY)
    request = make_request(history=[PriorTurn("q?", "a" * 30)])

    LlmChatModel(transport, guard(), 5).answer(request)

    assert "<answer>aaaaa</answer>" in transport.prompts[0][0].user


def test_a_reply_that_is_not_an_answer_is_an_invalid_model_answer():
    with pytest.raises(InvalidModelAnswer):
        LlmChatModel(RecordingTransport("no json"), guard(), 1000).answer(make_request())


def test_a_failing_transport_is_retried_then_reported_as_unavailable():
    class Down:
        calls = 0

        def complete(self, prompt: ModelPrompt, effort: ModelEffort) -> str:
            self.calls += 1
            raise TransientModelError

    transport = Down()

    with pytest.raises(ModelUnavailable):
        LlmChatModel(transport, guard(), 1000).answer(make_request())

    assert transport.calls == 2


def test_a_full_bulkhead_rejects_the_call_without_reaching_the_transport():
    transport = RecordingTransport(GOOD_REPLY)
    shared = guard(bulkhead=1)
    held = shared._bulkhead.acquire(blocking=False)

    with pytest.raises(ModelUnavailable, match="bulkhead"):
        LlmChatModel(transport, shared, 1000).answer(make_request())

    assert held and transport.prompts == []


def test_the_chat_shares_the_open_circuit_breaker_with_other_callers():
    shared = ModelGuard(CircuitBreaker("model", 1, 60), RetryPolicy(1, 0, 0), 2)

    def fail() -> str:
        raise TransientModelError

    with pytest.raises(ModelUnavailable):
        shared.call(fail)
    transport = RecordingTransport(GOOD_REPLY)

    with pytest.raises(ModelUnavailable, match="circuit breaker"):
        LlmChatModel(transport, shared, 1000).answer(make_request())

    assert transport.prompts == []


def mock_answer(behavior: str, sections: int = 2) -> str:
    request = make_request(sections=[make_section(n) for n in range(1, sections + 1)])
    prompt = build_chat_prompt(request, 1000)
    return MockChatTransport(behavior).complete(
        ModelPrompt(prompt.system, prompt.user, []), ModelEffort.HIGH
    )


@pytest.mark.parametrize("behavior", ["complete", "clarify", "clarify_no_dominant"])
def test_the_mock_answers_from_the_first_section(behavior: str):
    reply = json.loads(mock_answer(behavior))

    assert reply == {
        "outcome": "answered",
        "answer": "The care guide section 'Heading 1' covers this question.",
        "cited": ["S1"],
    }


def test_the_mock_says_not_in_kb_when_there_are_no_sections():
    reply = json.loads(mock_answer("complete", sections=0))

    assert (reply["outcome"], reply["cited"]) == ("not_in_kb", [])


def test_the_mock_can_fail_like_a_timeout_or_answer_with_garbage():
    with pytest.raises(TransientModelError):
        mock_answer("unavailable")
    assert mock_answer("malformed") == "this is not json"


def test_the_mock_is_chosen_for_the_mock_provider_and_wired_through_the_guard():
    chat = create_chat_model(make_settings(model_mock_behavior="malformed"), guard())

    with pytest.raises(InvalidModelAnswer):
        chat.answer(make_request())


def test_the_mock_unavailable_behaviour_degrades_through_retries():
    chat = create_chat_model(make_settings(model_mock_behavior="unavailable"), guard())

    with pytest.raises(ModelUnavailable):
        chat.answer(make_request())


def openrouter_settings(**overrides):
    return make_settings(
        model_provider="openrouter",
        openrouter_api_key="k",
        openrouter_model="vision/model",
        **overrides,
    )


def test_the_chat_uses_its_own_openrouter_model_and_timeout_when_configured():
    settings = openrouter_settings(openrouter_chat_model="text/model", chat_model_timeout_seconds=3)

    transport = create_chat_model(settings, guard())._transport

    assert isinstance(transport, OpenRouterTransport)
    assert transport._model == "text/model"
    assert transport._http.timeout == httpx.Timeout(connect=2.0, read=3, write=3, pool=2.0)


def test_the_chat_falls_back_to_the_diagnosis_model_name():
    transport = create_chat_model(openrouter_settings(), guard())._transport

    assert transport._model == "vision/model"
    assert transport._http.timeout.read == 8.0


def test_the_diagnosis_transport_keeps_its_own_model_and_timeout():
    settings = openrouter_settings(openrouter_chat_model="text/model", chat_model_timeout_seconds=3)

    transport = create_transport(settings)

    assert transport._model == "vision/model" and transport._http.timeout.read == 8.0


def test_the_diagnosis_and_the_chat_can_share_one_guard():
    settings = make_settings()
    shared = create_model_guard(settings)

    diagnosis = create_diagnosis_model(settings, NoPhotos(), shared)
    chat = create_chat_model(settings, shared)

    assert diagnosis._guard is chat._guard is shared


def test_the_diagnosis_builds_its_own_guard_when_none_is_given():
    diagnosis = create_diagnosis_model(make_settings(), NoPhotos())

    assert isinstance(diagnosis._guard, ModelGuard)
