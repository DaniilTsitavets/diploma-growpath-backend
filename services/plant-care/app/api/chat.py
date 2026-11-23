import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.chat_schemas import ChatHistoryResponse, ChatTurnRequest, ChatTurnResponse
from app.api.dependencies import current_user_id, get_chat_service
from app.api.problems import identity_required, invalid_request, not_found, problem, responses
from app.service.chat import ChatService

router = APIRouter(prefix="/v1/journals/{journal_id}/chat/turns", tags=["chat"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Chat = Annotated[ChatService, Depends(get_chat_service)]


ASK_ERRORS = responses(
    {200: {"model": ChatTurnResponse}},
    identity_required(),
    not_found("The user has no such journal, or the turn id belongs to another journal"),
    problem(409, "The AI quota of the current period is used up", "ai-quota-exhausted"),
    invalid_request("A path or body parameter is invalid, or the question is unusable"),
)
HISTORY_ERRORS = responses(
    identity_required(),
    not_found("The user has no such journal"),
    invalid_request("A query parameter is invalid, or the cursor is not valid", "invalid-cursor"),
)


@router.post("", status_code=201, responses=ASK_ERRORS)
def ask(
    journal_id: uuid.UUID,
    body: ChatTurnRequest,
    response: Response,
    user_id: UserId,
    service: Chat,
) -> ChatTurnResponse:
    """Ask a care question; the answer cites knowledge-base sections.

    A repeated id returns the stored turn (200). When the quota service or the model is
    unavailable the outcome is `sources_only` (200): the sections found, nothing stored, no quota
    spent, and the same id may be sent again.
    """
    reply = service.ask(user_id, journal_id, body.id, body.question)
    if not reply.created:
        response.status_code = 200
    if reply.turn is None:
        return ChatTurnResponse.sources_only(journal_id, body, reply.related)
    return ChatTurnResponse.of_turn(reply.turn)


@router.get("", responses=HISTORY_ERRORS)
def list_turns(
    journal_id: uuid.UUID,
    user_id: UserId,
    service: Chat,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> ChatHistoryResponse:
    """The chat history of the journal, newest first; pass `next_cursor` as `cursor` for more."""
    return ChatHistoryResponse.of(service.history(user_id, journal_id, limit, cursor))
