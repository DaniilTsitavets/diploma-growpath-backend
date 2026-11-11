import json
import re
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from app.service.chat_model import ChatOutcome, Citation, ModelChatAnswer
from app.service.diagnosis_model import InvalidModelAnswer
from app.service.knowledge_model import RetrievedSection

ANSWER_LIMIT = 4000
NOT_IN_KB_TEXT = (
    "I could not find this in the care guides I have, so I would rather not guess. "
    "Try asking in another way, or ask an expert."
)

AnswerText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=ANSWER_LIMIT)
]

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_REFERENCE = re.compile(r"^S([1-9][0-9]*)$")


class _ChatPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outcome: ChatOutcome
    answer: AnswerText
    cited: list[str] = []


@dataclass(frozen=True)
class ResolvedAnswer:
    outcome: ChatOutcome
    answer: str
    sources: list[Citation]


def parse_chat_answer(raw: str) -> ModelChatAnswer:
    """Validate the model output; anything but an outcome with an answer is rejected."""
    text = raw.strip()
    if fenced := _FENCE.match(text):
        text = fenced.group(1)
    try:
        payload = _ChatPayload.model_validate(json.loads(text))
    except (ValueError, ValidationError) as error:
        raise InvalidModelAnswer("the model output is not a valid chat answer") from error
    return ModelChatAnswer(payload.outcome, payload.answer, payload.cited)


def resolve_answer(answer: ModelChatAnswer, sections: list[RetrievedSection]) -> ResolvedAnswer:
    """Turn the model's references into citations of the sections it was given.

    References outside the given sections are dropped, and an answer that keeps none of them is
    not trusted: it becomes `not_in_kb`. The other outcomes cite nothing.
    """
    if answer.outcome is not ChatOutcome.ANSWERED:
        return ResolvedAnswer(answer.outcome, answer.answer, [])
    sources = _citations(answer.cited, sections)
    if not sources:
        return ResolvedAnswer(ChatOutcome.NOT_IN_KB, NOT_IN_KB_TEXT, [])
    return ResolvedAnswer(ChatOutcome.ANSWERED, answer.answer, sources)


def _citations(references: list[str], sections: list[RetrievedSection]) -> list[Citation]:
    numbers: list[int] = []
    for reference in references:
        match = _REFERENCE.match(reference.strip())
        if match and int(match[1]) <= len(sections) and int(match[1]) not in numbers:
            numbers.append(int(match[1]))
    return [
        Citation(
            rank,
            sections[number - 1].id,
            sections[number - 1].document_title,
            sections[number - 1].heading,
            sections[number - 1].source_url,
        )
        for rank, number in enumerate(numbers, start=1)
    ]
