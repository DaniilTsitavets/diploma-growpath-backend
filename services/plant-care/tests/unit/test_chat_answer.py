import json

import pytest

from app.service.chat_answer import (
    ANSWER_LIMIT,
    NOT_IN_KB_TEXT,
    parse_chat_answer,
    resolve_answer,
)
from app.service.chat_model import ChatOutcome, ModelChatAnswer
from app.service.diagnosis_model import InvalidModelAnswer
from tests.unit.knowledge_fakes import make_section

SECTIONS = [make_section(1), make_section(2), make_section(3)]


def reply(**fields) -> str:
    return json.dumps({"outcome": "answered", "answer": "Water weekly.", "cited": ["S1"], **fields})


def test_a_valid_reply_is_parsed():
    parsed = parse_chat_answer(reply(cited=["S2", "S1"]))

    assert parsed == ModelChatAnswer(ChatOutcome.ANSWERED, "Water weekly.", ["S2", "S1"])


def test_a_fenced_reply_and_surrounding_whitespace_are_accepted():
    assert parse_chat_answer(f"\n```json\n{reply()}\n```\n").answer == "Water weekly."


def test_the_answer_is_stripped_and_extra_fields_are_ignored():
    parsed = parse_chat_answer(reply(answer="  Water.  ", confidence=0.9))

    assert parsed.answer == "Water."


def test_cited_defaults_to_nothing():
    raw = json.dumps({"outcome": "not_in_kb", "answer": "I do not know."})

    assert parse_chat_answer(raw).cited == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "",
        "[]",
        "null",
        json.dumps({"answer": "x"}),
        json.dumps({"outcome": "answered"}),
        json.dumps({"outcome": "sources_only", "answer": "x"}),
        json.dumps({"outcome": "answered", "answer": "   "}),
        json.dumps({"outcome": "answered", "answer": "x" * (ANSWER_LIMIT + 1)}),
        json.dumps({"outcome": "answered", "answer": "x", "cited": "S1"}),
        json.dumps({"outcome": "answered", "answer": "x", "cited": [1]}),
    ],
)
def test_anything_else_is_rejected(raw: str):
    with pytest.raises(InvalidModelAnswer):
        parse_chat_answer(raw)


def test_valid_references_become_ranked_citations_of_the_given_sections():
    resolved = resolve_answer(ModelChatAnswer(ChatOutcome.ANSWERED, "Text", ["S3", "S1"]), SECTIONS)

    assert resolved.outcome is ChatOutcome.ANSWERED and resolved.answer == "Text"
    assert [(s.rank, s.section_id, s.section_heading) for s in resolved.sources] == [
        (1, SECTIONS[2].id, "Heading 3"),
        (2, SECTIONS[0].id, "Heading 1"),
    ]
    assert resolved.sources[0].source_url == "https://example.test/3"
    assert resolved.sources[0].document_title == "Document 3"


@pytest.mark.parametrize("cited", [[], ["S4"], ["S0"], ["s1"], ["1"], ["S1; DROP"], ["S-1"]])
def test_an_answer_without_a_valid_citation_is_not_trusted(cited: list[str]):
    resolved = resolve_answer(ModelChatAnswer(ChatOutcome.ANSWERED, "Invented.", cited), SECTIONS)

    assert (resolved.outcome, resolved.answer, resolved.sources) == (
        ChatOutcome.NOT_IN_KB,
        NOT_IN_KB_TEXT,
        [],
    )


def test_without_any_sections_an_answer_cannot_be_cited():
    resolved = resolve_answer(ModelChatAnswer(ChatOutcome.ANSWERED, "Invented.", ["S1"]), [])

    assert resolved.outcome is ChatOutcome.NOT_IN_KB


@pytest.mark.parametrize("outcome", [ChatOutcome.NOT_IN_KB, ChatOutcome.OUT_OF_SCOPE])
def test_refusals_keep_their_text_and_cite_nothing(outcome: ChatOutcome):
    resolved = resolve_answer(ModelChatAnswer(outcome, "Not my topic.", ["S1"]), SECTIONS)

    assert (resolved.outcome, resolved.answer, resolved.sources) == (outcome, "Not my topic.", [])
