import base64
import json

import httpx
import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError
from pydantic import SecretStr

from app.llm.bedrock import BedrockTransport
from app.llm.mock import MockTransport
from app.llm.openrouter import OpenRouterTransport
from app.llm.transport import ModelPrompt, PermanentModelError, TransientModelError
from app.service.consensus import decide
from app.service.diagnosis_answer import parse_answer
from app.service.diagnosis_model import Image
from app.service.model import ModelEffort
from tests.unit.fake_model import RULE

PROMPT = ModelPrompt("system text", "user text", [Image("image/png", b"\x89PNG-bytes")])
SECRET = "sk-secret-value"


def openrouter(handler) -> OpenRouterTransport:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenRouterTransport(
        http, "https://router.test/chat", SecretStr(SECRET), "vision/model", 1500
    )


def completion(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_openrouter_sends_the_prompt_the_photos_as_data_urls_and_the_effort():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return completion("{}")

    assert openrouter(handler).complete(PROMPT, ModelEffort.HIGH) == "{}"

    (request,) = seen
    body = json.loads(request.content)
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    assert body["model"] == "vision/model"
    assert body["reasoning"] == {"effort": "high"}
    assert body["max_tokens"] == 1500
    assert body["messages"][0] == {"role": "system", "content": "system text"}
    text_part, image_part = body["messages"][1]["content"]
    assert text_part == {"type": "text", "text": "user text"}
    expected = "data:image/png;base64," + base64.b64encode(b"\x89PNG-bytes").decode()
    assert image_part["image_url"]["url"] == expected


@pytest.mark.parametrize(("temperature", "sent"), [(None, False), (0.7, True), (0.0, True)])
def test_openrouter_sends_a_temperature_only_when_one_is_asked_for(
    temperature: float | None, sent: bool
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return completion("{}")

    openrouter(handler).complete(PROMPT, ModelEffort.MEDIUM, temperature)

    assert ("temperature" in seen[0]) is sent
    if sent:
        assert seen[0]["temperature"] == temperature


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_openrouter_throttling_and_server_errors_are_transient(status: int):
    with pytest.raises(TransientModelError):
        openrouter(lambda request: httpx.Response(status)).complete(PROMPT, ModelEffort.MEDIUM)


@pytest.mark.parametrize("status", [400, 401, 404])
def test_openrouter_other_client_errors_are_permanent(status: int):
    with pytest.raises(PermanentModelError):
        openrouter(lambda request: httpx.Response(status)).complete(PROMPT, ModelEffort.MEDIUM)


def test_openrouter_timeouts_are_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TransientModelError):
        openrouter(handler).complete(PROMPT, ModelEffort.MEDIUM)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        httpx.Response(200, text="not json"),
    ],
)
def test_openrouter_an_unexpected_body_is_permanent(answer: httpx.Response):
    with pytest.raises(PermanentModelError):
        openrouter(lambda request: answer).complete(PROMPT, ModelEffort.MEDIUM)


def test_openrouter_logs_the_call_without_prompt_answer_or_key(caplog: pytest.LogCaptureFixture):
    with caplog.at_level("INFO"):
        openrouter(lambda request: completion("secret answer")).complete(PROMPT, ModelEffort.MEDIUM)

    logged = " ".join(str(record.__dict__) for record in caplog.records)
    assert "outgoing call to model" in logged
    assert SECRET not in logged
    assert "secret answer" not in logged
    assert "user text" not in logged


class StubBedrock:
    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def converse(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def bedrock(client: StubBedrock) -> BedrockTransport:
    return BedrockTransport(
        client, "model-id", 1500, {ModelEffort.MEDIUM: 1024, ModelEffort.HIGH: 4096}
    )


def converse_answer(*blocks: dict) -> dict:
    return {"output": {"message": {"role": "assistant", "content": list(blocks)}}}


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Converse")


def test_bedrock_converse_gets_the_prompt_the_image_and_a_budget_by_effort():
    client = StubBedrock(converse_answer({"text": "{}"}))

    assert bedrock(client).complete(PROMPT, ModelEffort.HIGH) == "{}"

    (call,) = client.calls
    assert call["modelId"] == "model-id"
    assert call["system"] == [{"text": "system text"}]
    assert call["inferenceConfig"] == {"maxTokens": 4096 + 1500}
    assert call["additionalModelRequestFields"]["thinking"]["budget_tokens"] == 4096
    text_block, image_block = call["messages"][0]["content"]
    assert text_block == {"text": "user text"}
    assert image_block == {"image": {"format": "png", "source": {"bytes": b"\x89PNG-bytes"}}}


def test_bedrock_reasoning_blocks_are_not_part_of_the_answer():
    client = StubBedrock(
        converse_answer(
            {"reasoningContent": {"reasoningText": {"text": "thinking"}}}, {"text": "A"}
        )
    )

    assert bedrock(client).complete(PROMPT, ModelEffort.MEDIUM) == "A"


@pytest.mark.parametrize("code", ["ThrottlingException", "ModelTimeoutException"])
def test_bedrock_throttling_is_transient(code: str):
    with pytest.raises(TransientModelError):
        bedrock(StubBedrock(error=client_error(code))).complete(PROMPT, ModelEffort.MEDIUM)


def test_bedrock_a_rejected_request_is_permanent():
    with pytest.raises(PermanentModelError):
        bedrock(StubBedrock(error=client_error("AccessDeniedException"))).complete(
            PROMPT, ModelEffort.MEDIUM
        )


def test_bedrock_connection_problems_are_transient():
    error = ConnectTimeoutError(endpoint_url="https://bedrock.test")

    with pytest.raises(TransientModelError):
        bedrock(StubBedrock(error=error)).complete(PROMPT, ModelEffort.MEDIUM)


@pytest.mark.parametrize("response", [{}, converse_answer({"reasoningContent": {}})])
def test_bedrock_an_answer_without_text_is_permanent(response: dict):
    with pytest.raises(PermanentModelError):
        bedrock(StubBedrock(response)).complete(PROMPT, ModelEffort.MEDIUM)


def test_bedrock_an_unsupported_image_type_is_permanent():
    prompt = ModelPrompt("s", "u", [Image("image/bmp", b"BM")])

    with pytest.raises(PermanentModelError):
        bedrock(StubBedrock(converse_answer({"text": "{}"}))).complete(prompt, ModelEffort.MEDIUM)


FIRST_ROUND = ModelPrompt("s", "<plant>Basil</plant>", [])
SECOND_ROUND = ModelPrompt("s", "<plant>Basil</plant>\n<clarification>", [])


def mock_result(behavior: str, prompt: ModelPrompt):
    answer = parse_answer(MockTransport(behavior).complete(prompt, ModelEffort.MEDIUM))
    return decide([answer], 1, RULE, clarified=prompt is SECOND_ROUND)


def test_the_mock_completes_at_once_by_default():
    result = mock_result("complete", FIRST_ROUND)

    assert result.has_dominant_candidate and result.recommendation


def test_the_mock_asks_first_and_completes_after_the_answer():
    first = mock_result("clarify", FIRST_ROUND)
    second = mock_result("clarify", SECOND_ROUND)

    assert first.clarifying_question and first.roots_photo_requested
    assert not first.has_dominant_candidate
    assert second.has_dominant_candidate and second.recommendation


def test_the_mock_can_finish_without_a_dominant_candidate():
    second = mock_result("clarify_no_dominant", SECOND_ROUND)

    assert not second.has_dominant_candidate and second.recommendation
    assert second.escalation_offered


def test_bedrock_leaves_the_temperature_to_the_thinking_mode():
    client = StubBedrock(converse_answer({"text": "{}"}))

    bedrock(client).complete(PROMPT, ModelEffort.MEDIUM, temperature=0.7)

    assert "temperature" not in client.calls[0]["inferenceConfig"]


def test_the_split_mock_alternates_the_first_cause_by_call():
    transport = MockTransport("split")

    firsts = [
        parse_answer(transport.complete(FIRST_ROUND, ModelEffort.MEDIUM)).candidates[0].cause
        for _ in range(4)
    ]

    assert firsts == ["Overwatering", "Root rot", "Overwatering", "Root rot"]


def test_two_split_mock_samples_have_no_dominant_candidate():
    transport = MockTransport("split")
    answers = [parse_answer(transport.complete(FIRST_ROUND, ModelEffort.MEDIUM)) for _ in range(2)]

    result = decide(answers, 2, RULE, clarified=False)

    assert result.has_dominant_candidate is False
    assert [c.cause for c in result.candidates][:2] == ["Overwatering", "Root rot"]


def test_the_mock_can_fail_like_a_timeout():
    with pytest.raises(TransientModelError):
        MockTransport("unavailable").complete(FIRST_ROUND, ModelEffort.MEDIUM)


def test_the_mock_can_answer_with_garbage():
    reply = MockTransport("malformed").complete(FIRST_ROUND, ModelEffort.MEDIUM)

    assert reply == "this is not json"
