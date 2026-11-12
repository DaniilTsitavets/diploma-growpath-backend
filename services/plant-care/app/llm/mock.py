import itertools
import json

from app.llm.transport import ModelPrompt, TransientModelError
from app.service.model import ModelEffort

DOMINANT = {
    "candidates": [
        {
            "cause": "Overwatering",
            "rationale": "The soil stays wet and the pot feels heavy.",
            "support": "high",
        },
        {"cause": "Poor drainage", "rationale": "Water lingers in the pot.", "support": "low"},
    ],
    "recommendation": "Let the top 2-3 cm of soil dry out before watering again.",
    "clarifying_question": None,
    "request_roots_photo": False,
}
NOT_DOMINANT = {
    **DOMINANT,
    "candidates": [
        {"cause": "Overwatering", "rationale": "The soil is wet.", "support": "medium"},
        {
            "cause": "Root rot",
            "rationale": "The symptoms could also come from damaged roots.",
            "support": "medium",
        },
    ],
}
ROOT_ROT_FIRST = {
    **DOMINANT,
    "candidates": [
        {"cause": "Root rot", "rationale": "The roots may be damaged.", "support": "high"},
        {"cause": "Overwatering", "rationale": "The soil is wet.", "support": "low"},
    ],
}
SPLIT_BY_PARITY = (DOMINANT, ROOT_ROT_FIRST)
QUESTION = {
    "candidates": [
        {
            "cause": "Overwatering",
            "rationale": "Wet soil, but the cause is unclear.",
            "support": "medium",
        },
        {
            "cause": "Root rot",
            "rationale": "Soft lower leaves would point to it.",
            "support": "medium",
        },
    ],
    "recommendation": None,
    "clarifying_question": "Do the lower leaves feel soft, and does the soil smell sour?",
    "request_roots_photo": True,
}


class MockTransport:
    """A deterministic stand-in for a model, for tests and for showing failures.

    `behavior` selects the reply: `complete` (a dominant diagnosis at once), `clarify` (a question
    first, a dominant diagnosis after the user's answer), `clarify_no_dominant` (a question first,
    then a diagnosis whose top candidates are equally supported, which offers the expert),
    `split` (dominant replies whose first cause alternates with the call, so parallel samples
    disagree), `unavailable` (every call fails like a timeout) and `malformed` (the reply is not
    JSON). The second round is recognised by the <clarification> block of the prompt.
    """

    def __init__(self, behavior: str) -> None:
        self._behavior = behavior
        self._calls = itertools.count()

    def complete(
        self, prompt: ModelPrompt, effort: ModelEffort, temperature: float | None = None
    ) -> str:
        if self._behavior == "unavailable":
            raise TransientModelError("mock model is unavailable")
        if self._behavior == "malformed":
            return "this is not json"
        if self._behavior == "split":
            return json.dumps(SPLIT_BY_PARITY[next(self._calls) % 2])
        asks_first = self._behavior in ("clarify", "clarify_no_dominant")
        if asks_first and "<clarification>" not in prompt.user:
            return json.dumps(QUESTION)
        return json.dumps(NOT_DOMINANT if self._behavior == "clarify_no_dominant" else DOMINANT)
