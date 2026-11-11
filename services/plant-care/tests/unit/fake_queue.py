import json
import uuid

from app.service.consumer import QueueMessage, QueueUnavailable
from tests.unit.fakes import NOW


class FakeQueue:
    def __init__(self, log: list[str] | None = None) -> None:
        self.messages: list[QueueMessage] = []
        self.deleted: list[QueueMessage] = []
        self.log = log if log is not None else []
        self.receive_fails = False
        self.delete_fails = False
        self.check_fails = False
        self.last_receive: tuple[int, int] | None = None

    def send(self, event_type: str | None, body: dict | str, **attributes: str) -> QueueMessage:
        raw = body if isinstance(body, str) else json.dumps(body)
        message_attributes = dict(attributes)
        if event_type is not None:
            message_attributes["event_type"] = event_type
        message = QueueMessage(
            f"m-{len(self.messages)}", f"r-{len(self.messages)}", raw, message_attributes
        )
        self.messages.append(message)
        return message

    def receive(self, max_messages: int, wait_seconds: int) -> list[QueueMessage]:
        self.last_receive = (max_messages, wait_seconds)
        if self.receive_fails:
            raise QueueUnavailable("queue is down")
        return [m for m in self.messages if m not in self.deleted][:max_messages]

    def delete(self, message: QueueMessage) -> None:
        if self.delete_fails:
            raise QueueUnavailable("queue is down")
        self.deleted.append(message)
        self.log.append("delete")

    def check(self) -> None:
        if self.check_fails:
            raise QueueUnavailable("queue is down")


def order_placed_body(**overrides) -> dict:
    fields = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": NOW.isoformat(),
        "order_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "item_id": str(uuid.uuid4()),
        "item_type": "pack",
        "plant_species": "Basil",
        "seed_count": 5,
        "total_paid_cents": 2000,
        "discount_applied": True,
        "placed_at": NOW.isoformat(),
    }
    return {**fields, **overrides}
