import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_entitlement_service
from app.api.schemas import EntitlementResponse
from app.service.entitlements import EntitlementService

router = APIRouter(prefix="/v1/entitlements", tags=["entitlements"])


@router.get("/{user_id}")
def get_entitlement(
    user_id: uuid.UUID,
    service: Annotated[EntitlementService, Depends(get_entitlement_service)],
) -> EntitlementResponse:
    """The tier and the quotas of the user, computed from paid orders and reservations."""
    return EntitlementResponse.of(user_id, service.get(user_id))
