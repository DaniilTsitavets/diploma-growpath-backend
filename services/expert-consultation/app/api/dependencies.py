import re
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.service.directory import ROLE_GROUPS, Role
from app.service.expert_tickets import ExpertTicketService
from app.service.tickets import TicketService
from app.service.users import UserAdminService

EXPERT_GROUP = "experts"


def get_user_admin_service(request: Request) -> UserAdminService:
    return request.app.state.user_admin_service


def get_ticket_service(request: Request) -> TicketService:
    return request.app.state.ticket_service


def get_expert_ticket_service(request: Request) -> ExpertTicketService:
    return request.app.state.expert_ticket_service


def parse_groups(header: str | None) -> set[str]:
    """The gateway renders the `cognito:groups` array as `a,b` or `[a, b]`."""
    return {group for group in re.split(r"[\s,\[\]'\"]+", header or "") if group}


@dataclass(frozen=True)
class Identity:
    user_id: uuid.UUID
    groups: frozenset[str]


def current_identity(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_groups: Annotated[str | None, Header()] = None,
) -> Identity:
    """The caller of the request.

    The gateway is expected to set X-User-Id from the Cognito `sub` claim and X-User-Groups from
    `cognito:groups` (comma separated) of the verified token. This is the one place that knows it.
    """
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    try:
        user_id = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-User-Id is not a valid user id") from None
    return Identity(user_id, frozenset(parse_groups(x_user_groups)))


def current_expert_id(identity: Annotated[Identity, Depends(current_identity)]) -> uuid.UUID:
    if EXPERT_GROUP not in identity.groups:
        raise HTTPException(status_code=403, detail="the experts group is required")
    return identity.user_id


def current_admin_id(identity: Annotated[Identity, Depends(current_identity)]) -> uuid.UUID:
    if ROLE_GROUPS[Role.ADMIN] not in identity.groups:
        raise HTTPException(status_code=403, detail="the admins group is required")
    return identity.user_id
