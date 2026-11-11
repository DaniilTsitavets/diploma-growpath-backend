import pytest

from app.service.tsquery import build_tsquery, quote_lexeme


def test_lexemes_are_quoted_and_joined_with_or():
    assert build_tsquery(["basil", "water"]) == "'basil' | 'water'"


def test_repeated_lexemes_appear_once_in_their_first_position():
    assert build_tsquery(["water", "basil", "water"]) == "'water' | 'basil'"


@pytest.mark.parametrize("lexemes", [[], [""], ["", ""]])
def test_nothing_to_search_for_gives_none(lexemes: list[str]):
    assert build_tsquery(lexemes) is None


@pytest.mark.parametrize(
    ("lexeme", "quoted"),
    [
        ("it's", "'it''s'"),
        ("a\\b", "'a\\\\b'"),
        ("'", "''''"),
        ("\\'", "'\\\\'''"),
    ],
)
def test_quotes_and_backslashes_are_escaped(lexeme: str, quoted: str):
    assert quote_lexeme(lexeme) == quoted


@pytest.mark.parametrize(
    "lexeme",
    [
        "a&b",
        "a|b",
        "!a",
        "a:*",
        "(a)",
        "a<->b",
        "a<2>b",
        "http://x.test/a?b=(c)&d",
        "a b",
        "&|!:*()<>",
    ],
)
def test_operator_characters_stay_inside_the_quotes(lexeme: str):
    assert build_tsquery([lexeme]) == f"'{lexeme}'"


def test_a_lexeme_cannot_break_out_of_its_quotes():
    query = build_tsquery(["x' | 'y", "z\\' | 'w"])

    assert query == "'x'' | ''y' | 'z\\\\'' | ''w'"


def test_a_very_long_question_builds_one_operand_per_distinct_lexeme():
    lexemes = [f"word{number}" for number in range(1000)]

    query = build_tsquery(lexemes)

    assert query is not None
    assert query.count(" | ") == 999
