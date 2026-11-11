import uuid
from datetime import datetime

from pydantic import BaseModel

from app.service.model import Entitlement, Reservation, ReservationStatus, Tier


class OwnerRequest(BaseModel):
    user_id: uuid.UUID


class AiReservationRequest(OwnerRequest):
    reservation_id: uuid.UUID


class ExpertReservationRequest(OwnerRequest):
    escalation_id: uuid.UUID


class AiReservationResponse(BaseModel):
    reservation_id: uuid.UUID
    status: ReservationStatus
    expires_at: datetime

    @classmethod
    def of(cls, reservation: Reservation) -> "AiReservationResponse":
        return cls(
            reservation_id=reservation.id,
            status=reservation.status,
            expires_at=reservation.expires_at,
        )


class ExpertReservationResponse(BaseModel):
    escalation_id: uuid.UUID
    status: ReservationStatus
    expires_at: datetime

    @classmethod
    def of(cls, reservation: Reservation) -> "ExpertReservationResponse":
        return cls(
            escalation_id=reservation.id,
            status=reservation.status,
            expires_at=reservation.expires_at,
        )


class QuotaUsageResponse(BaseModel):
    used: int
    limit: int


class EntitlementResponse(BaseModel):
    user_id: uuid.UUID
    tier: Tier
    tier_expires_at: datetime | None
    ai_quota_today: QuotaUsageResponse
    expert_quota_30_days: QuotaUsageResponse

    @classmethod
    def of(cls, user_id: uuid.UUID, entitlement: Entitlement) -> "EntitlementResponse":
        return cls(
            user_id=user_id,
            tier=entitlement.tier,
            tier_expires_at=entitlement.tier_expires_at,
            ai_quota_today=QuotaUsageResponse(**vars(entitlement.ai)),
            expert_quota_30_days=QuotaUsageResponse(**vars(entitlement.expert)),
        )
