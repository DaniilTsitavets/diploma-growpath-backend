import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import current_user_id, get_update_service
from app.api.schemas import FeedResponse, SubmitUpdateRequest, UpdateResponse
from app.service.updates import UpdateService

router = APIRouter(prefix="/v1/journals/{journal_id}/updates", tags=["updates"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Updates = Annotated[UpdateService, Depends(get_update_service)]


@router.post("", status_code=201, responses={200: {"model": UpdateResponse}})
def submit_update(
    journal_id: uuid.UUID,
    body: SubmitUpdateRequest,
    response: Response,
    user_id: UserId,
    service: Updates,
) -> UpdateResponse:
    """Submit an update; repeating the request with the same id returns the stored one (200)."""
    submitted = service.submit(user_id, body.to_new_update(journal_id))
    if not submitted.created:
        response.status_code = 200
    return UpdateResponse.of(submitted.item)


@router.get("")
def list_updates(
    journal_id: uuid.UUID,
    user_id: UserId,
    service: Updates,
    limit: Annotated[int | None, Query(ge=1)] = None,
    before: Annotated[str | None, Query(min_length=1)] = None,
) -> FeedResponse:
    """The update feed, newest first; pass `next_cursor` of a page as `before` for the next one."""
    return FeedResponse.of(service.feed(user_id, journal_id, limit, before))
