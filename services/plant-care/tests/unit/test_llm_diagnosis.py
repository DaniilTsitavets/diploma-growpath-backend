import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm.diagnosis import LlmDiagnosisModel
from app.llm.factory import create_diagnosis_model, create_model_guard, create_transport
from app.llm.guard import ModelGuard
from app.llm.mock import MockTransport
from app.llm.openrouter import OpenRouterTransport
from app.llm.transport import ModelPrompt, PermanentModelError
from app.resilience import CircuitBreaker, RetryPolicy
from app.service.diagnosis_model import (
    Image,
    InvalidModelAnswer,
    ModelUnavailable,
    PhotoUnreadable,
)
from app.service.model import ModelEffort
from tests.unit.diagnosis_data import make_request
from tests.unit.settings import make_settings


class FakePhotoReader:
    def __init__(self, unreadable: bool = False) -> None:
        self.read_keys: list[str] = []
        self.unreadable = unreadable

    def read(self, storage_key: str) -> Image:
        if self.unreadable:
            raise PhotoUnreadable("gone")
        self.read_keys.append(storage_key)
        return Image("image/jpeg", storage_key.encode())


class RecordingTransport:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[tuple[ModelPrompt, ModelEffort]] = []

    def complete(self, prompt: ModelPrompt, effort: ModelEffort) -> str:
        self.prompts.append((prompt, effort))
        return self.reply


def guard() -> ModelGuard:
    return ModelGuard(CircuitBreaker("model", 5, 60), RetryPolicy(2, 0, 0), 2, sleep=lambda _: None)


GOOD_REPLY = json.dumps(
    {
        "candidates": [{"cause": "Overwatering", "rationale": "Wet", "support": "high"}],
        "recommendation": "Water less.",
    }
)


def test_the_photos_are_read_in_kind_order_and_sent_with_the_prompt_and_effort():
    photos, transport = FakePhotoReader(), RecordingTransport(GOOD_REPLY)

    answer = LlmDiagnosisModel(transport, guard(), photos).diagnose(make_request())

    ((prompt, effort),) = transport.prompts
    assert photos.read_keys == ["key/overview", "key/soil"]
    assert [image.data for image in prompt.images] == [b"key/overview", b"key/soil"]
    assert "<plant>Basil</plant>" in prompt.user
    assert effort is ModelEffort.HIGH
    assert answer.recommendation == "Water less."


def test_a_malformed_reply_is_an_invalid_answer():
    model = LlmDiagnosisModel(RecordingTransport("nope"), guard(), FakePhotoReader())

    with pytest.raises(InvalidModelAnswer):
        model.diagnose(make_request())


def test_an_unreadable_photo_fails_before_the_model_is_called():
    transport = RecordingTransport(GOOD_REPLY)
    model = LlmDiagnosisModel(transport, guard(), FakePhotoReader(unreadable=True))

    with pytest.raises(PhotoUnreadable):
        model.diagnose(make_request())

    assert transport.prompts == []


def test_a_failing_transport_is_unavailable():
    class Rejecting:
        def complete(self, prompt: ModelPrompt, effort: ModelEffort) -> str:
            raise PermanentModelError

    model = LlmDiagnosisModel(Rejecting(), guard(), FakePhotoReader())

    with pytest.raises(ModelUnavailable):
        model.diagnose(make_request())


def test_the_default_provider_is_the_mock_and_needs_no_key():
    settings = make_settings()

    assert isinstance(create_transport(settings), MockTransport)
    assert isinstance(create_diagnosis_model(settings, FakePhotoReader()), LlmDiagnosisModel)


def test_openrouter_is_built_from_settings():
    settings = make_settings(
        model_provider="openrouter", openrouter_api_key="k", openrouter_model="vision/model"
    )

    assert isinstance(create_transport(settings), OpenRouterTransport)


def test_bedrock_is_built_from_settings_with_the_default_credential_chain(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    settings = make_settings(model_provider="bedrock", bedrock_model_id="some.model-v1:0")

    transport = create_transport(settings)

    assert transport._client.meta.config.read_timeout == settings.model_timeout_seconds


def test_the_guard_takes_its_limits_from_settings():
    assert isinstance(create_model_guard(make_settings(model_bulkhead_size=1)), ModelGuard)


@pytest.mark.parametrize(
    "fields",
    [
        {"model_provider": "openrouter", "openrouter_model": "m"},
        {"model_provider": "openrouter", "openrouter_api_key": "", "openrouter_model": "m"},
        {"model_provider": "openrouter", "openrouter_api_key": "k"},
        {"model_provider": "bedrock"},
    ],
)
def test_a_provider_without_its_settings_is_refused_at_startup(fields: dict):
    with pytest.raises(ValidationError):
        make_settings(**fields)


def test_the_api_key_is_not_printed_with_the_settings():
    settings = make_settings(
        model_provider="openrouter", openrouter_api_key="sk-hidden", openrouter_model="m"
    )

    assert "sk-hidden" not in repr(settings)
    assert isinstance(settings, Settings)
