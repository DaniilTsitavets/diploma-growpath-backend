import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import current_user_id, get_diagnosis_service
from app.api.problems import identity_required, invalid_request, not_found, problem, responses
from app.api.schemas import ClarificationRequest, DiagnosisDetailResponse
from app.service.diagnosis import DiagnosisService

router = APIRouter(prefix="/v1/updates/{update_id}/diagnosis", tags=["diagnosis"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Diagnoses = Annotated[DiagnosisService, Depends(get_diagnosis_service)]

READ_ERRORS = responses(
    identity_required(),
    not_found("The user has no such update, or the update has no diagnosis"),
    invalid_request("A path parameter is invalid"),
)
RUN_ERRORS = responses(
    READ_ERRORS,
    problem(
        409,
        "Another attempt is running, or the AI quota of the current period is used up",
        "diagnosis-conflict",
        "ai-quota-exhausted",
    ),
    invalid_request("A path parameter is invalid, or the required photos are missing"),
)
CLARIFICATION_ERRORS = responses(
    READ_ERRORS,
    problem(409, "The diagnosis is not waiting for a clarification", "diagnosis-conflict"),
    invalid_request("The body is invalid, or the answer is unusable"),
)
DECLINE_ERRORS = responses(
    READ_ERRORS,
    problem(409, "No expert escalation is on offer", "diagnosis-conflict"),
)


@router.get("", responses=READ_ERRORS)
def get_diagnosis(
    update_id: uuid.UUID, user_id: UserId, service: Diagnoses
) -> DiagnosisDetailResponse:
    """The diagnosis of the update with its candidate causes."""
    return DiagnosisDetailResponse.of_diagnosis(service.get(user_id, update_id))


@router.post("", responses=RUN_ERRORS)
def run_diagnosis(
    update_id: uuid.UUID, user_id: UserId, service: Diagnoses
) -> DiagnosisDetailResponse:
    """Run the diagnosis or retry it after a failure; the request waits for the model."""
    return DiagnosisDetailResponse.of_diagnosis(service.run(user_id, update_id))


@router.post("/clarification", responses=CLARIFICATION_ERRORS)
def answer_clarification(
    update_id: uuid.UUID, body: ClarificationRequest, user_id: UserId, service: Diagnoses
) -> DiagnosisDetailResponse:
    """Answer the clarifying question; the re-evaluation is free and returns the new diagnosis."""
    return DiagnosisDetailResponse.of_diagnosis(
        service.answer_clarification(user_id, update_id, body.answer)
    )


@router.post("/escalation-decline", responses=DECLINE_ERRORS)
def decline_escalation(
    update_id: uuid.UUID, user_id: UserId, service: Diagnoses
) -> DiagnosisDetailResponse:
    """Decline the expert the AI offered after the clarification."""
    return DiagnosisDetailResponse.of_diagnosis(service.decline_escalation(user_id, update_id))
