from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import get_catalog_service
from app.api.schemas import ItemResponse
from app.service.catalog import CatalogService

router = APIRouter(prefix="/v1/items", tags=["catalog"])

CACHE_CONTROL = "public, max-age=60"
CACHE_CONTROL_HEADER = {
    "Cache-Control": {
        "description": "The catalog is public and may be cached for a minute",
        "schema": {"type": "string", "const": CACHE_CONTROL},
    }
}
UNAVAILABLE = {
    "description": "The catalog database is unreachable",
    "headers": {
        "Retry-After": {
            "description": "Seconds to wait before retrying",
            "schema": {"type": "integer"},
        }
    },
}


@router.get(
    "",
    responses={
        200: {"description": "Active items", "headers": CACHE_CONTROL_HEADER},
        503: UNAVAILABLE,
    },
)
def list_items(
    response: Response,
    service: Annotated[CatalogService, Depends(get_catalog_service)],
    label: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
) -> list[ItemResponse]:
    """Public: the gateway sets no identity for this route."""
    items = [ItemResponse.model_validate(entry) for entry in service.list_items(label)]
    response.headers["Cache-Control"] = CACHE_CONTROL
    return items


@router.get(
    "/{slug}",
    responses={
        200: {"description": "The active item", "headers": CACHE_CONTROL_HEADER},
        404: {"description": "No active item has this slug, or the slug is malformed"},
        503: UNAVAILABLE,
    },
)
def get_item(
    slug: str,
    response: Response,
    service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> ItemResponse:
    """Public: the gateway sets no identity for this route."""
    item = ItemResponse.model_validate(service.get_item(slug))
    response.headers["Cache-Control"] = CACHE_CONTROL
    return item
