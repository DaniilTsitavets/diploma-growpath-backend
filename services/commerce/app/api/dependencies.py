import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.service.catalog import CatalogService
from app.service.purchase import PurchaseService


def get_purchase_service(request: Request) -> PurchaseService:
    return request.app.state.purchase_service


def get_catalog_service(request: Request) -> CatalogService:
    return request.app.state.catalog_service


def current_user_id(x_user_id: Annotated[str | None, Header()] = None) -> uuid.UUID:
    """The API gateway sets X-User-Id from the Cognito `sub` claim of the verified token."""
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-User-Id is not a valid user id") from None
