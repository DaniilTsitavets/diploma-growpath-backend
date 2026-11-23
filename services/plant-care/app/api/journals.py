import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_user_id, get_journal_service
from app.api.problems import identity_required, invalid_request, not_found, responses
from app.api.schemas import JournalListResponse, JournalResponse, PlantingRequest
from app.service.journals import JournalService

router = APIRouter(prefix="/v1/journals", tags=["journals"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Journals = Annotated[JournalService, Depends(get_journal_service)]

LIST_ERRORS = responses(
    identity_required(),
    invalid_request("A query parameter is invalid, or the cursor is not valid", "invalid-cursor"),
)
ITEM_ERRORS = responses(
    identity_required(),
    not_found("The user has no such journal"),
    invalid_request("A path or body parameter is invalid"),
)


@router.get("", responses=LIST_ERRORS)
def list_journals(
    user_id: UserId,
    service: Journals,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> JournalListResponse:
    """The journals of the user, newest first; pass `next_cursor` of a page as `cursor` for more."""
    return JournalListResponse.of(service.page_for_user(user_id, limit, cursor))


@router.get("/{journal_id}", responses=ITEM_ERRORS)
def get_journal(journal_id: uuid.UUID, user_id: UserId, service: Journals) -> JournalResponse:
    return JournalResponse.of(service.get(journal_id, user_id))


@router.put("/{journal_id}/planting", responses=ITEM_ERRORS)
def record_planting(
    journal_id: uuid.UUID, body: PlantingRequest, user_id: UserId, service: Journals
) -> JournalResponse:
    """Record the planting date; the checkpoint windows are computed from it."""
    return JournalResponse.of(service.plant(journal_id, user_id, body.planted_on))
