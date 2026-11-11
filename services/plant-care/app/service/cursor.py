import base64
import uuid
from datetime import datetime

from app.service.errors import InvalidCursor
from app.service.model import FeedCursor

SEPARATOR = "|"


def encode_cursor(cursor: FeedCursor) -> str:
    raw = f"{cursor.created_at.isoformat()}{SEPARATOR}{cursor.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(token: str) -> FeedCursor:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        created_at, update_id = raw.split(SEPARATOR)
        cursor = FeedCursor(datetime.fromisoformat(created_at), uuid.UUID(update_id))
    except ValueError as error:
        raise InvalidCursor from error
    if cursor.created_at.tzinfo is None:
        raise InvalidCursor
    return cursor
