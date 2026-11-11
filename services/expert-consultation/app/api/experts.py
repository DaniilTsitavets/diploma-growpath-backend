import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import current_expert_id, get_expert_ticket_service, get_ticket_service
from app.api.schemas import AnswerRequest, AnswerResponse, QueueResponse, TicketResponse
from app.service.expert_tickets import ExpertTicketService
from app.service.tickets import TicketService

router = APIRouter(prefix="/v1/expert/tickets", tags=["expert"])

ExpertId = Annotated[uuid.UUID, Depends(current_expert_id)]
Tickets = Annotated[TicketService, Depends(get_ticket_service)]
ExpertTickets = Annotated[ExpertTicketService, Depends(get_expert_ticket_service)]


@router.get("")
def open_tickets(
    _: ExpertId,
    service: ExpertTickets,
    limit: Annotated[int | None, Query(ge=1)] = None,
    after: Annotated[str | None, Query(min_length=1)] = None,
) -> QueueResponse:
    """Open tickets that have not expired, soonest deadline first.

    Pass `next_cursor` of a page as `after` for the next one.
    """
    return QueueResponse.of(service.queue(limit, after))


@router.get("/{ticket_id}")
def get_ticket(ticket_id: uuid.UUID, _: ExpertId, service: ExpertTickets) -> TicketResponse:
    """The ticket with its context and presigned URLs for its photos, issued at this read."""
    return TicketResponse.of(service.view(ticket_id))


@router.post("/{ticket_id}/answer", status_code=201, responses={200: {"model": AnswerResponse}})
def answer_ticket(
    ticket_id: uuid.UUID,
    body: AnswerRequest,
    response: Response,
    expert_id: ExpertId,
    service: Tickets,
) -> AnswerResponse:
    """Answer an open ticket.

    Repeating the request as the same expert returns the stored answer (200). 409 when another
    expert answered or the ticket expired.
    """
    result = service.answer(ticket_id, expert_id, body.body)
    if not result.created:
        response.status_code = 200
    return AnswerResponse.of(result.ticket)
