import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Self

from app.config import Settings
from app.service.errors import LockTimeout, QueueUnavailable
from app.service.model import NewPlacedOrder, QueueMessage, Reservation, ReservationStatus, Tier
from app.service.quota import QuotaKind, QuotaService, ai_quota_kind, expert_quota_kind
from app.service.tiers import TierPolicy

UNREACHABLE_DATABASE = "postgresql+psycopg://nobody@127.0.0.1:1/none"
ORDER_QUEUE_URL = "http://localstack:4566/000000000000/entitlement-order-placed"
TICKET_QUEUE_URL = "http://localstack:4566/000000000000/entitlement-ticket-events"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
RESERVED = ReservationStatus.RESERVED
COMMITTED = ReservationStatus.COMMITTED
RELEASED = ReservationStatus.RELEASED


def make_settings(**overrides) -> Settings:
    fields = {
        "database_url": UNREACHABLE_DATABASE,
        "order_placed_queue_url": ORDER_QUEUE_URL,
        "ticket_events_queue_url": TICKET_QUEUE_URL,
    }
    return Settings(**{**fields, **overrides})


def make_tiers(settings: Settings | None = None) -> TierPolicy:
    settings = settings or make_settings()
    return TierPolicy(settings.hobbyist_threshold_cents, settings.premium_threshold_cents)


def make_ai_kind(**limits: int) -> QuotaKind:
    settings = make_settings()
    by_tier = {
        Tier.STARTER: settings.assumed_ai_limit_per_day_starter,
        Tier.HOBBYIST: settings.assumed_ai_limit_per_day_hobbyist,
        Tier.PREMIUM: settings.assumed_ai_limit_per_day_premium,
    }
    by_tier.update({Tier(name): value for name, value in limits.items()})
    return ai_quota_kind(by_tier, timedelta(minutes=settings.ai_reservation_ttl_minutes))


def make_expert_kind(**limits: int) -> QuotaKind:
    settings = make_settings()
    by_tier = {
        Tier.STARTER: settings.assumed_expert_limit_per_30_days_starter,
        Tier.HOBBYIST: settings.assumed_expert_limit_per_30_days_hobbyist,
        Tier.PREMIUM: settings.assumed_expert_limit_per_30_days_premium,
    }
    by_tier.update({Tier(name): value for name, value in limits.items()})
    return expert_quota_kind(by_tier, timedelta(hours=settings.expert_reservation_ttl_hours))


class FakeReservations:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, Reservation] = {}
        self.locked_users: list[uuid.UUID] = []
        self.inserted_ttls: list[timedelta] = []
        self.lock_times_out = False
        self.insert_loses_to: Reservation | None = None
        self.last_transition_unexpired_only: bool | None = None

    def seed(
        self,
        user_id: uuid.UUID,
        status: ReservationStatus = RESERVED,
        unexpired: bool = True,
        reservation_id: uuid.UUID | None = None,
    ) -> Reservation:
        reservation = Reservation(
            id=reservation_id or uuid.uuid4(),
            user_id=user_id,
            status=status,
            expires_at=NOW + timedelta(minutes=5 if unexpired else -5),
            unexpired=unexpired,
        )
        self.rows[reservation.id] = reservation
        return reservation

    def lock_user(self, user_id: uuid.UUID) -> None:
        if self.lock_times_out:
            raise LockTimeout
        self.locked_users.append(user_id)

    def get(self, reservation_id: uuid.UUID) -> Reservation | None:
        return self.rows.get(reservation_id)

    def get_owned(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> Reservation | None:
        row = self.rows.get(reservation_id)
        return row if row is not None and row.user_id == user_id else None

    def count_used(self, user_id: uuid.UUID) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.user_id == user_id
            and (row.status is COMMITTED or (row.status is RESERVED and row.unexpired))
        )

    def insert(
        self, reservation_id: uuid.UUID, user_id: uuid.UUID, ttl: timedelta
    ) -> Reservation | None:
        if self.insert_loses_to is not None:
            self.rows[reservation_id] = self.insert_loses_to
            return None
        self.inserted_ttls.append(ttl)
        return self.seed(user_id, reservation_id=reservation_id)

    def transition(
        self,
        reservation_id: uuid.UUID,
        user_id: uuid.UUID,
        target: ReservationStatus,
        sources: frozenset[ReservationStatus],
        unexpired_only: bool,
    ) -> Reservation | None:
        self.last_transition_unexpired_only = unexpired_only
        row = self.get_owned(reservation_id, user_id)
        if row is None or row.status not in sources or (unexpired_only and not row.unexpired):
            return None
        moved = Reservation(row.id, row.user_id, target, row.expires_at, row.unexpired)
        self.rows[row.id] = moved
        return moved


class FakePlacedOrders:
    def __init__(self) -> None:
        self.orders: dict[uuid.UUID, NewPlacedOrder] = {}
        self.tier_expiry: datetime | None = None
        self.expiry_queries: list[tuple[uuid.UUID, int, int]] = []

    def pay(self, user_id: uuid.UUID, cents: int) -> None:
        order = NewPlacedOrder(uuid.uuid4(), user_id, cents, NOW)
        self.orders[order.order_id] = order

    def add(self, order: NewPlacedOrder) -> bool:
        if order.order_id in self.orders:
            return False
        self.orders[order.order_id] = order
        return True

    def window_total(self, user_id: uuid.UUID) -> int:
        return sum(o.total_paid_cents for o in self.orders.values() if o.user_id == user_id)

    def tier_expires_at(self, user_id: uuid.UUID, total: int, threshold: int) -> datetime | None:
        self.expiry_queries.append((user_id, total, threshold))
        return self.tier_expiry


class FakeUnitOfWork:
    def __init__(self, log: list[str] | None = None) -> None:
        self.placed_orders = FakePlacedOrders()
        self.ai_reservations = FakeReservations()
        self.expert_reservations = FakeReservations()
        self.commits = 0
        self.log = log if log is not None else []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1
        self.log.append("commit")


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


def ticket_body(escalation_id: uuid.UUID, user_id: uuid.UUID, **overrides) -> dict:
    fields = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": NOW.isoformat(),
        "ticket_id": str(uuid.uuid4()),
        "escalation_id": str(escalation_id),
        "user_id": str(user_id),
    }
    return {**fields, **overrides}


def make_quota(kind: QuotaKind, uow: FakeUnitOfWork) -> QuotaService:
    return QuotaService(kind, lambda: uow, make_tiers())
