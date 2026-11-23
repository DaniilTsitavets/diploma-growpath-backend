import re
import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.service.chat import ChatService
from app.service.diagnosis import DiagnosisService
from app.service.escalations import EscalationService
from app.service.journals import JournalService
from app.service.plants import PlantService
from app.service.updates import UpdateService

CUSTOMER_GROUP = "users"
STAFF_GROUPS = frozenset({"experts", "admins"})


def get_journal_service(request: Request) -> JournalService:
    return request.app.state.journal_service


def get_update_service(request: Request) -> UpdateService:
    return request.app.state.update_service


def get_diagnosis_service(request: Request) -> DiagnosisService:
    return request.app.state.diagnosis_service


def get_escalation_service(request: Request) -> EscalationService:
    return request.app.state.escalation_service


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_plant_service(request: Request) -> PlantService:
    return request.app.state.plant_service


def parse_groups(header: str | None) -> set[str]:
    """The gateway renders the `cognito:groups` array as `a,b` or `[a, b]`."""
    return {group for group in re.split(r"[\s,\[\]'\"]+", header or "") if group}


def current_user_id(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_groups: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """The customer behind the request.

    The gateway sets X-User-Id from the Cognito `sub` claim and X-User-Groups from
    `cognito:groups` of the verified token. Experts and admins have no journals: they are refused.
    """
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    try:
        user_id = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-User-Id is not a valid user id") from None
    groups = parse_groups(x_user_groups)
    if CUSTOMER_GROUP not in groups or groups & STAFF_GROUPS:
        raise HTTPException(status_code=403, detail="this role cannot use this endpoint")
    return user_id
