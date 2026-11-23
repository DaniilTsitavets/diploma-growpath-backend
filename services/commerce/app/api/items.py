from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import get_catalog_service
from app.api.errors import problem_responses
from app.api.schemas import ItemPage, ItemResponse, Problem
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
    "model": Problem,
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
        200: {"description": "A page of active items", "headers": CACHE_CONTROL_HEADER},
        **problem_responses(422),
        503: UNAVAILABLE,
    },
)
def list_items(
    response: Response,
    service: Annotated[CatalogService, Depends(get_catalog_service)],
    label: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            description="Page size; defaults to 50 and is clamped to 100",
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=200,
            description="Opaque `next_cursor` of the previous page; the items are ordered by slug",
        ),
    ] = None,
) -> ItemPage:
    """Public: the gateway sets no identity for this route."""
    page = service.list_items(label, limit, cursor)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return ItemPage(
        items=[ItemResponse.model_validate(entry) for entry in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{slug}",
    responses={
        200: {"description": "The active item", "headers": CACHE_CONTROL_HEADER},
        **problem_responses(404),
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
