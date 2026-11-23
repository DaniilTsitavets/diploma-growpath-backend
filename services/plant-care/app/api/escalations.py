import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import current_user_id, get_escalation_service
from app.api.problems import (
    identity_required,
    invalid_request,
    not_found,
    problem,
    responses,
    unavailable,
)
from app.api.schemas import EscalationDetailResponse, EscalationRequest
from app.service.escalations import EscalationService

router = APIRouter(prefix="/v1/updates/{update_id}/escalation", tags=["escalation"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Escalations = Annotated[EscalationService, Depends(get_escalation_service)]

REQUEST_ERRORS = responses(
    {200: {"model": EscalationDetailResponse}},
    identity_required(),
    not_found("The user has no such update"),
    problem(
        409,
        "No expert is on offer, or the expert quota of the current period is used up",
        "escalation-not-offered",
        "expert-quota-exhausted",
    ),
    invalid_request("A path or body parameter is invalid, or the question is unusable"),
    unavailable("The quota service did not answer"),
)
GET_ERRORS = responses(
    identity_required(),
    not_found("The user has no such update, or the update has no escalation"),
    invalid_request("A path parameter is invalid"),
)


@router.post("", status_code=201, responses=REQUEST_ERRORS)
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


@router.get("", responses=GET_ERRORS)
def get_escalation(
    update_id: uuid.UUID, user_id: UserId, service: Escalations
) -> EscalationDetailResponse:
    """The latest escalation of the update with the expert's answer, once there is one."""
    return EscalationDetailResponse.of_escalation(service.get(user_id, update_id))
