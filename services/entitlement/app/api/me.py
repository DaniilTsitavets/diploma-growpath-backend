import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import current_user_id, get_entitlement_service
from app.api.errors import problem_responses
from app.api.schemas import EntitlementResponse
from app.service.entitlements import EntitlementService

router = APIRouter(prefix="/v1/me", tags=["me"])


@router.get("/entitlement", responses=problem_responses(401, 403, 422))
def get_my_entitlement(
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
    service: Annotated[EntitlementService, Depends(get_entitlement_service)],
) -> EntitlementResponse:
    """The tier and the quotas of the calling customer."""
    return EntitlementResponse.of(user_id, service.get(user_id))
