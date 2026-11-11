import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import current_user_id, get_escalation_service
from app.api.schemas import EscalationDetailResponse, EscalationRequest
from app.service.escalations import EscalationService

router = APIRouter(prefix="/v1/updates/{update_id}/escalation", tags=["escalation"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Escalations = Annotated[EscalationService, Depends(get_escalation_service)]


@router.post("", status_code=201, responses={200: {"model": EscalationDetailResponse}})
def request_escalation(
    update_id: uuid.UUID,
    response: Response,
    user_id: UserId,
    service: Escalations,
    body: EscalationRequest | None = None,
) -> EscalationDetailResponse:
    """Ask an expert, after the offer of a diagnosis or with a `user_question` of your own.

    Repeating the request returns the active escalation (200).
    """
    requested = service.request(user_id, update_id, None if body is None else body.user_question)
    if not requested.created:
        response.status_code = 200
    return EscalationDetailResponse.of_escalation(requested.escalation)


@router.get("")
def get_escalation(
    update_id: uuid.UUID, user_id: UserId, service: Escalations
) -> EscalationDetailResponse:
    """The latest escalation of the update with the expert's answer, once there is one."""
    return EscalationDetailResponse.of_escalation(service.get(user_id, update_id))
