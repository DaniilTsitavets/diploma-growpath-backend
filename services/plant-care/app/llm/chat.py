from app.llm.guard import ModelGuard
from app.llm.transport import ModelPrompt, ModelTransport
from app.service.chat_answer import parse_chat_answer
from app.service.chat_model import ChatRequest, ModelChatAnswer
from app.service.chat_prompt import build_chat_prompt


class LlmChatModel:
    """The ChatModel port over any provider: prompt, guarded call, parsed answer."""

    def __init__(
        self, transport: ModelTransport, guard: ModelGuard, history_answer_chars: int
    ) -> None:
        self._transport = transport
        self._guard = guard
        self._history_answer_chars = history_answer_chars

    def answer(self, request: ChatRequest) -> ModelChatAnswer:
        prompt = build_chat_prompt(request, self._history_answer_chars)
        model_prompt = ModelPrompt(prompt.system, prompt.user, [])
        raw = self._guard.call(lambda: self._transport.complete(model_prompt, request.effort))
        return parse_chat_answer(raw)
