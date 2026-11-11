from dataclasses import dataclass
from typing import Protocol

from app.service.model import Candidate, ModelEffort, Photo, UpdateRecord


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
class ModelAnswer:
    """What the model concluded: either a clarifying question or a recommendation."""

    has_dominant_candidate: bool
    candidates: list[Candidate]
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


class DiagnosisModel(Protocol):
    def diagnose(self, request: DiagnosisRequest) -> ModelAnswer:
        """Raises ModelFailure."""
        ...


class PhotoReader(Protocol):
    def read(self, storage_key: str) -> Image:
        """The photo bytes. Raises PhotoUnreadable."""
        ...
