from dataclasses import dataclass
from typing import Protocol

from app.service.dominance import Support
from app.service.model import ModelEffort, Photo, UpdateRecord


class ModelFailure(Exception):
    """The model could not produce a usable answer; the diagnosis degrades."""


class ModelUnavailable(ModelFailure):
    """Timeout, provider error, open breaker or a full bulkhead."""


class InvalidModelAnswer(ModelFailure):
    """The model answered with something that is not a valid diagnosis."""


class PhotoUnreadable(ModelFailure):
    """A photo of the update could not be read from the object store or is not usable."""


@dataclass(frozen=True)
class Image:
    media_type: str
    data: bytes


@dataclass(frozen=True)
class RankedCandidate:
    """A cause as the model ranked it; `support` is used to decide dominance and never stored."""

    cause: str
    rationale: str
    support: Support


@dataclass(frozen=True)
class ModelAnswer:
    """What the model concluded: either a clarifying question or a recommendation."""

    candidates: list[RankedCandidate]
    recommendation: str | None
    clarifying_question: str | None
    request_roots_photo: bool


@dataclass(frozen=True)
class DiagnosisRequest:
    species: str
    update: UpdateRecord
    photos: list[Photo]
    effort: ModelEffort
    clarifying_question: str | None
    clarifying_answer: str | None
    temperature: float | None = None
    """None leaves the temperature to the provider; parallel samples set one so they can differ."""


class DiagnosisModel(Protocol):
    def diagnose(self, request: DiagnosisRequest) -> ModelAnswer:
        """Raises ModelFailure."""
        ...


class PhotoReader(Protocol):
    def read(self, storage_key: str) -> Image:
        """The photo bytes. Raises PhotoUnreadable."""
        ...
