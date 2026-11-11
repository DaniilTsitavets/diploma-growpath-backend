import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Tier(StrEnum):
    STARTER = "starter"
    HOBBYIST = "hobbyist"
    PREMIUM = "premium"


class ReservationStatus(StrEnum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    user_id: uuid.UUID
    status: ReservationStatus
    expires_at: datetime
    unexpired: bool
    """Whether `expires_at` was still in the future on the database clock when the row was read."""


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    receipt_handle: str
    body: str
    attributes: Mapping[str, str]


@dataclass(frozen=True)
class Reserved:
    reservation: Reservation
    created: bool


@dataclass(frozen=True)
class NewPlacedOrder:
    order_id: uuid.UUID
    user_id: uuid.UUID
    total_paid_cents: int
    placed_at: datetime


@dataclass(frozen=True)
class QuotaUsage:
    used: int
    limit: int


@dataclass(frozen=True)
class Entitlement:
    tier: Tier
    tier_expires_at: datetime | None
    ai: QuotaUsage
    expert: QuotaUsage
