import base64
import uuid
from datetime import UTC, datetime

import pytest

from app.service.cursor import InvalidCursor, decode_cursor, encode_cursor
from app.service.model import QueueCursor

CURSOR = QueueCursor(datetime(2026, 11, 14, 12, 30, 5, 123456, tzinfo=UTC), uuid.uuid4())


def token(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode()


def test_a_cursor_survives_a_round_trip_with_its_microseconds():
    assert decode_cursor(encode_cursor(CURSOR)) == CURSOR


def test_the_token_is_url_safe():
    assert encode_cursor(CURSOR).replace("-", "").replace("_", "").replace("=", "").isalnum()


@pytest.mark.parametrize(
    "bad",
    [
        "!!!",
        token("only-one-part"),
        token("2026-11-14T12:00:00+00:00|not-a-uuid"),
        token(f"not-a-time|{uuid.uuid4()}"),
        token(f"2026-11-14T12:00:00|{uuid.uuid4()}"),
        token(f"a|b|{uuid.uuid4()}"),
        "",
    ],
    ids=["not base64", "one part", "bad id", "bad time", "no zone", "three parts", "empty"],
)
def test_a_bad_token_is_an_invalid_cursor(bad: str):
    with pytest.raises(InvalidCursor):
        decode_cursor(bad)
