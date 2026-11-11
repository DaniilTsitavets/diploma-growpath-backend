from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_catalog_service
from app.api.schemas import ItemResponse
from app.service.catalog import CatalogService

router = APIRouter(prefix="/v1/items", tags=["catalog"])


@router.get("")
def list_items(
    service: Annotated[CatalogService, Depends(get_catalog_service)],
    label: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
) -> list[ItemResponse]:
    return [ItemResponse.model_validate(entry) for entry in service.list_items(label)]
