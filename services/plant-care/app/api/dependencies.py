import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.service.diagnosis import DiagnosisService
from app.service.escalations import EscalationService
from app.service.journals import JournalService
from app.service.updates import UpdateService


def get_journal_service(request: Request) -> JournalService:
    return request.app.state.journal_service


def get_update_service(request: Request) -> UpdateService:
    return request.app.state.update_service


def get_diagnosis_service(request: Request) -> DiagnosisService:
    return request.app.state.diagnosis_service


def get_escalation_service(request: Request) -> EscalationService:
    return request.app.state.escalation_service


def current_user_id(x_user_id: Annotated[str | None, Header()] = None) -> uuid.UUID:
    """The API gateway sets X-User-Id from the Cognito `sub` claim of the verified token."""
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-User-Id is not a valid user id") from None
