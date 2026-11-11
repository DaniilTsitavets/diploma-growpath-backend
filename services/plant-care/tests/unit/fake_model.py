from collections.abc import Callable

from app.service.diagnosis_model import DiagnosisRequest, ModelAnswer
from app.service.model import Candidate

CAUSES = [Candidate("Overwatering", "Wet soil"), Candidate("Poor drainage", "Heavy pot")]


def conclusion(dominant: bool = True) -> ModelAnswer:
    return ModelAnswer(dominant, CAUSES, "Water less.", None, False)


def question(roots_photo: bool = False) -> ModelAnswer:
    return ModelAnswer(False, CAUSES[:1], None, "Are the leaves soft?", roots_photo)


class FakeModel:
    """Plays its script answer by answer; an exception in the script is raised."""

    def __init__(self, *script: ModelAnswer | Exception) -> None:
        self.script = list(script)
        self.requests: list[DiagnosisRequest] = []
        self.during_call: Callable[[], None] | None = None

    def diagnose(self, request: DiagnosisRequest) -> ModelAnswer:
        self.requests.append(request)
        if self.during_call is not None:
            self.during_call()
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step
