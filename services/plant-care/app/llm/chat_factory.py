from app.config import Settings
from app.llm.chat import LlmChatModel
from app.llm.factory import create_transport
from app.llm.guard import ModelGuard
from app.llm.mock_chat import MockChatTransport
from app.llm.transport import ModelTransport
from app.service.chat_model import ChatModel


def _chat_transport(settings: Settings) -> ModelTransport:
    if settings.model_provider == "mock":
        return MockChatTransport(settings.model_mock_behavior)
    return create_transport(
        settings,
        timeout_seconds=settings.chat_model_timeout_seconds,
        openrouter_model=settings.openrouter_chat_model,
    )


def create_chat_model(settings: Settings, guard: ModelGuard) -> ChatModel:
    """The guard is the one of the diagnosis, so both share the circuit breaker and the bulkhead."""
    return LlmChatModel(_chat_transport(settings), guard, settings.chat_history_answer_chars)
