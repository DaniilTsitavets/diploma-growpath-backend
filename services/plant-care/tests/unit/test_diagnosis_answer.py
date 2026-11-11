import json

import pytest

from app.service.diagnosis_answer import parse_answer
from app.service.diagnosis_model import InvalidModelAnswer


def payload(**overrides) -> dict:
    fields = {
        "has_dominant_candidate": True,
        "candidates": [{"cause": "Overwatering", "rationale": "Wet soil"}],
        "recommendation": "Water less.",
        "clarifying_question": None,
        "request_roots_photo": False,
    }
    return {**fields, **overrides}


def parse(**overrides):
    return parse_answer(json.dumps(payload(**overrides)))


def test_a_complete_diagnosis_is_parsed():
    answer = parse()

    assert answer.has_dominant_candidate is True
    assert [(c.cause, c.rationale) for c in answer.candidates] == [("Overwatering", "Wet soil")]
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


def test_unknown_keys_are_ignored():
    assert parse(confidence=0.9).has_dominant_candidate is True


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        "{}",
        json.dumps(payload(recommendation=None)),
        json.dumps(payload(recommendation="  ")),
        json.dumps(payload(candidates=[])),
        json.dumps(payload(has_dominant_candidate="maybe")),
        json.dumps(payload(candidates=[{"cause": "x" * 201, "rationale": "r"}])),
        json.dumps(payload(candidates=[{"cause": "c", "rationale": "r" * 1001}])),
        json.dumps(payload(candidates=[{"cause": "c", "rationale": "r"}] * 6)),
        json.dumps(payload(recommendation="r" * 8001)),
        json.dumps(payload(recommendation=None, clarifying_question="q" * 501)),
    ],
)
def test_anything_else_is_rejected(raw: str):
    with pytest.raises(InvalidModelAnswer):
        parse_answer(raw)
