from collections.abc import Callable

from app.service.diagnosis_model import DiagnosisRequest, ModelAnswer, RankedCandidate
from app.service.dominance import DominanceRule, Support

RULE = DominanceRule(Support.HIGH, frozenset({Support.MEDIUM, Support.HIGH}))


def ranked(*causes: tuple[str, Support]) -> list[RankedCandidate]:
    return [RankedCandidate(cause, f"Why {cause}", support) for cause, support in causes]


def conclusion(
    dominant: bool = True, causes: list[RankedCandidate] | None = None, text: str = "Water less."
) -> ModelAnswer:
    """A recommendation; its supports make the first candidate dominant or not."""
    supports = (Support.HIGH, Support.LOW) if dominant else (Support.MEDIUM, Support.MEDIUM)
    default = ranked(("Overwatering", supports[0]), ("Poor drainage", supports[1]))
    return ModelAnswer(causes or default, text, None, False)


def question(roots_photo: bool = False, text: str = "Are the leaves soft?") -> ModelAnswer:
    candidates = ranked(("Overwatering", Support.MEDIUM), ("Root rot", Support.MEDIUM))
    return ModelAnswer(candidates, None, text, roots_photo)


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
