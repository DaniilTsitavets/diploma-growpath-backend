from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import Identity, current_identity
from app.api.errors import problem_responses
from app.api.schemas import MeResponse
from app.service.directory import role_of

router = APIRouter(prefix="/v1/me", tags=["identity"])


@router.get("", responses=problem_responses(401))
def me(identity: Annotated[Identity, Depends(current_identity)]) -> MeResponse:
    """Who the gateway says the caller is; answered from the headers without any lookup."""
    return MeResponse(
        user_id=identity.user_id, role=role_of(identity.groups), groups=sorted(identity.groups)
    )
