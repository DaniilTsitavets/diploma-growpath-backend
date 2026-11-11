import re
import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.service.expert_tickets import ExpertTicketService
from app.service.tickets import TicketService

EXPERT_GROUP = "experts"


def get_ticket_service(request: Request) -> TicketService:
    return request.app.state.ticket_service


def get_expert_ticket_service(request: Request) -> ExpertTicketService:
    return request.app.state.expert_ticket_service


def parse_groups(header: str | None) -> set[str]:
    """The gateway renders the `cognito:groups` array as `a,b` or `[a, b]`."""
    return {group for group in re.split(r"[\s,\[\]'\"]+", header or "") if group}


def current_expert_id(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_groups: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """The expert behind the request.

    The gateway is expected to set X-User-Id from the Cognito `sub` claim and X-User-Groups from
    `cognito:groups` (comma separated) of the verified token. This is the one place that knows it.
    """
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    try:
        expert_id = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-User-Id is not a valid user id") from None
    if EXPERT_GROUP not in parse_groups(x_user_groups):
        raise HTTPException(status_code=403, detail="the experts group is required")
    return expert_id
