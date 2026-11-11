import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.chat_schemas import ChatHistoryResponse, ChatTurnRequest, ChatTurnResponse
from app.api.dependencies import current_user_id, get_chat_service
from app.service.chat import ChatService

router = APIRouter(prefix="/v1/journals/{journal_id}/chat/turns", tags=["chat"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Chat = Annotated[ChatService, Depends(get_chat_service)]


@router.post("", status_code=201, responses={200: {"model": ChatTurnResponse}})
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


@router.get("")
def list_turns(
    journal_id: uuid.UUID,
    user_id: UserId,
    service: Chat,
    limit: Annotated[int | None, Query(ge=1)] = None,
    before: Annotated[str | None, Query(min_length=1)] = None,
) -> ChatHistoryResponse:
    """The chat history of the journal, newest first; pass `next_cursor` as `before` for more."""
    return ChatHistoryResponse.of(service.history(user_id, journal_id, limit, before))
