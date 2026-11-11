import json
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.service.diagnosis_model import InvalidModelAnswer, ModelAnswer
from app.service.model import Candidate

MAX_CANDIDATES = 5
Cause = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Rationale = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class _CandidatePayload(BaseModel):
    cause: Cause
    rationale: Rationale


class _AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    has_dominant_candidate: bool
    candidates: list[_CandidatePayload] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    recommendation: Text | None = Field(default=None, max_length=8000)
    clarifying_question: Text | None = Field(default=None, max_length=500)
    request_roots_photo: bool = False


def parse_answer(raw: str) -> ModelAnswer:
    """Validate the model output; anything else than a diagnosis object is rejected."""
    text = raw.strip()
    if fenced := _FENCE.match(text):
        text = fenced.group(1)
    try:
        payload = _AnswerPayload.model_validate(json.loads(text))
    except (ValueError, ValidationError) as error:
        raise InvalidModelAnswer("the model output is not a valid diagnosis") from error

    recommendation = payload.recommendation or None
    question = payload.clarifying_question or None
    if recommendation is None and question is None:
        raise InvalidModelAnswer("the model gave neither a recommendation nor a question")
    if recommendation is not None and not payload.candidates:
        raise InvalidModelAnswer("a recommendation without candidate causes")
    return ModelAnswer(
        has_dominant_candidate=payload.has_dominant_candidate,
        candidates=[Candidate(item.cause, item.rationale) for item in payload.candidates],
        recommendation=recommendation,
        clarifying_question=question,
        request_roots_photo=payload.request_roots_photo and question is not None,
    )
