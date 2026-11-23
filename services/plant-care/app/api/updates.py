import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import current_user_id, get_update_service
from app.api.problems import identity_required, invalid_request, not_found, problem, responses
from app.api.schemas import FeedResponse, SubmitUpdateRequest, UpdateResponse
from app.service.updates import UpdateService

router = APIRouter(prefix="/v1/journals/{journal_id}/updates", tags=["updates"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Updates = Annotated[UpdateService, Depends(get_update_service)]


SUBMIT_ERRORS = responses(
    {200: {"model": UpdateResponse}},
    identity_required(),
    not_found("The user has no such journal"),
    problem(409, "The AI quota of the current period is used up", "ai-quota-exhausted"),
    invalid_request("A path or body parameter is invalid"),
)
FEED_ERRORS = responses(
    identity_required(),
    not_found("The user has no such journal"),
    invalid_request("A query parameter is invalid, or the cursor is not valid", "invalid-cursor"),
)


@router.post("", status_code=201, responses=SUBMIT_ERRORS)
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


@router.get("", responses=FEED_ERRORS)
def list_updates(
    journal_id: uuid.UUID,
    user_id: UserId,
    service: Updates,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> FeedResponse:
    """The update feed, newest first; pass `next_cursor` of a page as `cursor` for the next one."""
    return FeedResponse.of(service.feed(user_id, journal_id, limit, cursor))
