from dataclasses import dataclass
from typing import Protocol

from app.service.diagnosis_model import Image
from app.service.model import ModelEffort


class ModelCallFailed(Exception):
    """A provider call failed; counted by the circuit breaker."""


class TransientModelError(ModelCallFailed):
    """Timeout, connection error, throttling or a 5xx answer: worth one more try."""


class PermanentModelError(ModelCallFailed):
    """The provider rejected the request or answered in an unusable shape."""


@dataclass(frozen=True)
class ModelPrompt:
    system: str
    user: str
    images: list[Image]


class ModelTransport(Protocol):
    def complete(self, prompt: ModelPrompt, effort: ModelEffort) -> str:
        """The text of the model's reply. Raises ModelCallFailed."""
        ...
