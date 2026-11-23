import re
import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.service.catalog import CatalogService
from app.service.purchase import PurchaseService

CUSTOMER_GROUP = "users"
NON_CUSTOMER_GROUPS = frozenset({"experts", "admins"})


def get_purchase_service(request: Request) -> PurchaseService:
    return request.app.state.purchase_service


def get_catalog_service(request: Request) -> CatalogService:
    return request.app.state.catalog_service


def parse_groups(header: str | None) -> set[str]:
    """The gateway renders the `cognito:groups` array as `a,b` or `[a, b]`."""
    return {group for group in re.split(r"[\s,\[\]'\"]+", header or "") if group}


def current_user_id(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_groups: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """The customer behind the request.

    The API gateway sets X-User-Id from the Cognito `sub` claim and X-User-Groups from
    `cognito:groups` of the verified token. Experts and admins are never customers, even when
    they also belong to `users`.
    """
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    try:
        user_id = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-User-Id is not a valid user id") from None
    groups = parse_groups(x_user_groups)
    if CUSTOMER_GROUP not in groups or groups & NON_CUSTOMER_GROUPS:
        raise HTTPException(status_code=403, detail="this role cannot use this endpoint")
    return user_id
