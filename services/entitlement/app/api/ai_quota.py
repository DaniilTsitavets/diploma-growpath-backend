import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import get_ai_quota
from app.api.schemas import AiReservationRequest, AiReservationResponse, OwnerRequest
from app.service.quota import QuotaService

router = APIRouter(prefix="/v1/quotas/ai/reservations", tags=["ai quota"])

AiQuota = Annotated[QuotaService, Depends(get_ai_quota)]


@router.post("", status_code=201, responses={200: {"model": AiReservationResponse}})
def reserve(
    body: AiReservationRequest, response: Response, quota: AiQuota
) -> AiReservationResponse:
    """Reserve one diagnosis; repeating the request with the same id returns it (200)."""
    reserved = quota.reserve(body.reservation_id, body.user_id)
    if not reserved.created:
        response.status_code = 200
    return AiReservationResponse.of(reserved.reservation)


@router.post("/{reservation_id}/commit")
def commit(reservation_id: uuid.UUID, body: OwnerRequest, quota: AiQuota) -> AiReservationResponse:
    return AiReservationResponse.of(quota.commit(reservation_id, body.user_id))


@router.post("/{reservation_id}/release")
def release(reservation_id: uuid.UUID, body: OwnerRequest, quota: AiQuota) -> AiReservationResponse:
    return AiReservationResponse.of(quota.release(reservation_id, body.user_id))
