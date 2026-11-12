import json

import pytest

from app.service.diagnosis_answer import parse_answer
from app.service.diagnosis_model import InvalidModelAnswer
from app.service.dominance import Support


def candidate(**overrides) -> dict:
    return {"cause": "c", "rationale": "r", "support": "low", **overrides}


def payload(**overrides) -> dict:
    fields = {
        "candidates": [{"cause": "Overwatering", "rationale": "Wet soil", "support": "high"}],
        "recommendation": "Water less.",
        "clarifying_question": None,
        "request_roots_photo": False,
    }
    return {**fields, **overrides}


def parse(**overrides):
    return parse_answer(json.dumps(payload(**overrides)))


def test_a_complete_diagnosis_is_parsed():
    answer = parse()

    assert [(c.cause, c.rationale, c.support) for c in answer.candidates] == [
        ("Overwatering", "Wet soil", Support.HIGH)
    ]
    assert answer.recommendation == "Water less."
    assert answer.clarifying_question is None


def test_a_clarifying_question_without_a_recommendation_is_parsed():
    answer = parse(
        recommendation=None, clarifying_question=" Soft leaves? ", request_roots_photo=True
    )

    assert answer.clarifying_question == "Soft leaves?"
    assert answer.request_roots_photo is True
    assert answer.recommendation is None


def test_the_roots_photo_request_needs_a_question():
    assert parse(request_roots_photo=True).request_roots_photo is False


def test_a_json_fence_around_the_object_is_accepted():
    raw = "```json\n" + json.dumps(payload()) + "\n```"

    assert parse_answer(raw).recommendation == "Water less."


def test_unknown_keys_are_ignored_and_the_flag_of_the_model_is_not_trusted():
    answer = parse(confidence=0.9, has_dominant_candidate=False)

    assert not hasattr(answer, "has_dominant_candidate")
    assert answer.recommendation == "Water less."


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        "{}",
        json.dumps(payload(recommendation=None)),
        json.dumps(payload(recommendation="  ")),
        json.dumps(payload(candidates=[])),
        json.dumps(payload(recommendation=None, clarifying_question="q", candidates=[])),
        json.dumps({"recommendation": "Water less."}),
        json.dumps(payload(candidates=[candidate(support="certain")])),
        json.dumps(payload(candidates=[candidate(support="HIGH")])),
        json.dumps(payload(candidates=[candidate(support=0.9)])),
        json.dumps(payload(candidates=[{"cause": "c", "rationale": "r"}])),
        json.dumps(payload(candidates=[candidate(cause="x" * 201)])),
        json.dumps(payload(candidates=[candidate(rationale="r" * 1001)])),
        json.dumps(payload(candidates=[candidate()] * 6)),
        json.dumps(payload(recommendation="r" * 8001)),
        json.dumps(payload(recommendation=None, clarifying_question="q" * 501)),
    ],
)
def test_anything_else_is_rejected(raw: str):
    with pytest.raises(InvalidModelAnswer):
        parse_answer(raw)
