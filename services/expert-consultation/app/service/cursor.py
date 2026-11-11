import base64
import uuid
from datetime import datetime

from app.service.errors import InvalidRequest
from app.service.model import QueueCursor

SEPARATOR = "|"


class InvalidCursor(InvalidRequest):
    def __init__(self) -> None:
        super().__init__("the cursor is not valid")


def encode_cursor(cursor: QueueCursor) -> str:
    raw = f"{cursor.expires_at.isoformat()}{SEPARATOR}{cursor.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(token: str) -> QueueCursor:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        expires_at, ticket_id = raw.split(SEPARATOR)
        cursor = QueueCursor(datetime.fromisoformat(expires_at), uuid.UUID(ticket_id))
    except ValueError as error:
        raise InvalidCursor from error
    if cursor.expires_at.tzinfo is None:
        raise InvalidCursor
    return cursor
