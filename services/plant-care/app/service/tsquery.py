from collections.abc import Iterable


def quote_lexeme(lexeme: str) -> str:
    """A tsquery operand in single quotes; inside them only `'` and `\\` are special."""
    return "'" + lexeme.replace("\\", "\\\\").replace("'", "''") + "'"


def build_tsquery(lexemes: Iterable[str]) -> str | None:
    """An OR query over already stemmed lexemes, or None when there is nothing to search for.

    The text is meant for a plain `CAST(... AS tsquery)`. Passing it through `to_tsquery` would
    parse it again, and lexemes of a question with a URL or brackets contain `( ) & :`.
    """
    unique = dict.fromkeys(lexeme for lexeme in lexemes if lexeme)
    return " | ".join(quote_lexeme(lexeme) for lexeme in unique) or None
