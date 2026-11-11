from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

Route = Callable[[str], None]


class QueueUnavailable(Exception):
    """The message queue did not answer."""


class InvalidMessage(Exception):
    """A queue message that cannot be understood; retrying will not help."""


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    receipt_handle: str
    body: str
    attributes: Mapping[str, str]


class MessageQueue(Protocol):
    def receive(self, max_messages: int, wait_seconds: int) -> list[QueueMessage]:
        """Long-poll for messages. Raises QueueUnavailable when the queue does not answer."""
        ...

    def delete(self, message: QueueMessage) -> None:
        """Acknowledge a message. Raises QueueUnavailable when the queue does not answer."""
        ...

    def check(self) -> None:
        """Raises QueueUnavailable when the queue cannot be reached."""
        ...
