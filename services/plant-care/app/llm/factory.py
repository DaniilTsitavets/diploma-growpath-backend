import boto3
import httpx
from botocore.config import Config

from app.config import Settings
from app.llm.bedrock import BedrockTransport
from app.llm.diagnosis import LlmDiagnosisModel
from app.llm.guard import ModelGuard
from app.llm.mock import MockTransport
from app.llm.openrouter import OpenRouterTransport
from app.llm.transport import ModelTransport
from app.resilience import CircuitBreaker, RetryPolicy
from app.service.diagnosis_model import DiagnosisModel, PhotoReader
from app.service.model import ModelEffort


def create_model_guard(settings: Settings) -> ModelGuard:
    breaker = CircuitBreaker(
        "model", settings.model_breaker_failure_threshold, settings.model_breaker_reset_seconds
    )
    retry = RetryPolicy(
        settings.model_max_attempts,
        settings.model_retry_base_delay_seconds,
        settings.model_retry_max_delay_seconds,
    )
    return ModelGuard(breaker, retry, settings.model_bulkhead_size)


def create_transport(
    settings: Settings,
    timeout_seconds: float | None = None,
    openrouter_model: str | None = None,
) -> ModelTransport:
    """The transport of the configured provider, with an optional timeout and OpenRouter model."""
    timeout = timeout_seconds or settings.model_timeout_seconds
    match settings.model_provider:
        case "openrouter":
            model = openrouter_model or settings.openrouter_model
            assert settings.openrouter_api_key is not None and model
            http = httpx.Client(
                timeout=httpx.Timeout(
                    connect=settings.model_connect_timeout_seconds,
                    read=timeout,
                    write=timeout,
                    pool=settings.model_connect_timeout_seconds,
                )
            )
            return OpenRouterTransport(
                http,
                settings.openrouter_url,
                settings.openrouter_api_key,
                model,
                settings.model_max_output_tokens,
            )
        case "bedrock":
            assert settings.bedrock_model_id
            client = boto3.client(
                "bedrock-runtime",
                region_name=settings.aws_region,
                config=Config(
                    connect_timeout=settings.model_connect_timeout_seconds,
                    read_timeout=timeout,
                    retries={"max_attempts": 1, "mode": "standard"},
                ),
            )
            return BedrockTransport(
                client,
                settings.bedrock_model_id,
                settings.model_max_output_tokens,
                {
                    ModelEffort.MEDIUM: settings.bedrock_thinking_budget_medium,
                    ModelEffort.HIGH: settings.bedrock_thinking_budget_high,
                },
            )
        case _:
            return MockTransport(settings.model_mock_behavior)


def create_diagnosis_model(
    settings: Settings, photos: PhotoReader, guard: ModelGuard | None = None
) -> DiagnosisModel:
    return LlmDiagnosisModel(
        create_transport(settings), guard or create_model_guard(settings), photos
    )
