import json
import logging
from collections.abc import Callable, Mapping
from contextvars import copy_context
from dataclasses import dataclass
from typing import Protocol

from growpath_observability import set_correlation_id

logger = logging.getLogger(__name__)

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


class QueueConsumer:
    """Handles queue messages by their `event_type` attribute and deletes them after the handler.

    A message whose handler fails stays in the queue; SQS redelivers it and moves it to the DLQ
    after `maxReceiveCount`.
    """

    def __init__(
        self,
        queue: MessageQueue,
        routes: Mapping[str, Route],
        ignored: frozenset[str],
        batch_size: int,
        wait_seconds: int,
    ) -> None:
        self._queue = queue
        self._routes = routes
        self._ignored = ignored
        self._batch_size = batch_size
        self._wait_seconds = wait_seconds

    def run_once(self) -> int:
        """Poll once; returns how many messages were received."""
        messages = self._queue.receive(self._batch_size, self._wait_seconds)
        for message in messages:
            copy_context().run(self._process, message)
        return len(messages)

    def _process(self, message: QueueMessage) -> None:
        set_correlation_id(_correlation_id(message))
        try:
            self._handle(message)
        except Exception:
            # Any failure, including a bug, must leave the message for redelivery and the DLQ.
            logger.exception(
                "message handling failed, left in the queue",
                extra={"message_id": message.message_id},
            )
            return
        try:
            self._queue.delete(message)
        except QueueUnavailable:
            logger.exception(
                "message was handled but not deleted, it will be redelivered",
                extra={"message_id": message.message_id},
            )

    def _handle(self, message: QueueMessage) -> None:
        event_type = message.attributes.get("event_type")
        if event_type is None:
            raise InvalidMessage("the message has no event_type attribute")
        route = self._routes.get(event_type)
        if route is not None:
            route(message.body)
        elif event_type in self._ignored:
            logger.info("event ignored by design", extra={"event_type": event_type})
        else:
            logger.warning(
                "event of an unknown type acknowledged", extra={"event_type": event_type}
            )


def _correlation_id(message: QueueMessage) -> str:
    if correlation_id := message.attributes.get("correlation_id"):
        return correlation_id
    try:
        event_id = json.loads(message.body).get("event_id")
    except (json.JSONDecodeError, AttributeError):
        event_id = None
    return event_id or message.message_id
