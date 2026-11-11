import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import get_expert_quota
from app.api.schemas import ExpertReservationRequest, ExpertReservationResponse, OwnerRequest
from app.service.quota import QuotaService

router = APIRouter(prefix="/v1/quotas/expert/reservations", tags=["expert quota"])

ExpertQuota = Annotated[QuotaService, Depends(get_expert_quota)]


@router.post("", status_code=201, responses={200: {"model": ExpertReservationResponse}})
def reserve(
    body: ExpertReservationRequest, response: Response, quota: ExpertQuota
) -> ExpertReservationResponse:
    """Reserve one expert escalation; repeating the request with the same id returns it (200)."""
    reserved = quota.reserve(body.escalation_id, body.user_id)
    if not reserved.created:
        response.status_code = 200
    return ExpertReservationResponse.of(reserved.reservation)


@router.post("/{escalation_id}/commit")
def commit(
    escalation_id: uuid.UUID, body: OwnerRequest, quota: ExpertQuota
) -> ExpertReservationResponse:
    return ExpertReservationResponse.of(quota.commit(escalation_id, body.user_id))


@router.post("/{escalation_id}/release")
def release(
    escalation_id: uuid.UUID, body: OwnerRequest, quota: ExpertQuota
) -> ExpertReservationResponse:
    return ExpertReservationResponse.of(quota.release(escalation_id, body.user_id))
