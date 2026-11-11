from app.llm.guard import ModelGuard
from app.llm.transport import ModelPrompt, ModelTransport
from app.service.diagnosis_answer import parse_answer
from app.service.diagnosis_model import DiagnosisRequest, ModelAnswer, PhotoReader
from app.service.diagnosis_prompt import build_prompt, ordered_photos


class LlmDiagnosisModel:
    """The DiagnosisModel port over any provider: prompt, photos, guarded call, parsed answer."""

    def __init__(self, transport: ModelTransport, guard: ModelGuard, photos: PhotoReader) -> None:
        self._transport = transport
        self._guard = guard
        self._photos = photos

    def diagnose(self, request: DiagnosisRequest) -> ModelAnswer:
        prompt = build_prompt(request)
        images = [self._photos.read(photo.storage_key) for photo in ordered_photos(request.photos)]
        model_prompt = ModelPrompt(prompt.system, prompt.user, images)
        raw = self._guard.call(lambda: self._transport.complete(model_prompt, request.effort))
        return parse_answer(raw)
