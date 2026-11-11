import base64
import uuid
from datetime import datetime

import pytest

from app.service.cursor import decode_cursor, encode_cursor
from app.service.errors import InvalidCursor
from app.service.model import FeedCursor
from tests.unit.fakes import NOW


def encoded(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode()


def test_a_cursor_survives_a_round_trip_with_microseconds():
    cursor = FeedCursor(NOW.replace(microsecond=123456), uuid.uuid4())

    assert decode_cursor(encode_cursor(cursor)) == cursor


@pytest.mark.parametrize(
    "token",
    [
        "not base64 at all",
        encoded("no separator"),
        encoded(f"yesterday|{uuid.uuid4()}"),
        encoded(f"{NOW.isoformat()}|not-a-uuid"),
        encoded(f"{datetime(2026, 11, 11, 12, 0).isoformat()}|{uuid.uuid4()}"),
        encoded(f"{NOW.isoformat()}|{uuid.uuid4()}|extra"),
    ],
    ids=["garbage", "no-separator", "bad-time", "bad-id", "naive-time", "extra-part"],
)
def test_a_malformed_cursor_is_rejected(token: str):
    with pytest.raises(InvalidCursor):
        decode_cursor(token)
