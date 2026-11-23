from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import get_plant_service
from app.api.plant_schemas import PlantProfileResponse
from app.api.problems import not_found, responses, unavailable
from app.service.plants import PlantService

router = APIRouter(prefix="/v1/plants", tags=["plants"])

Plants = Annotated[PlantService, Depends(get_plant_service)]
CACHE_CONTROL = "public, max-age=300"
RESPONSES = responses(
    {200: {"headers": {"Cache-Control": {"schema": {"type": "string"}}}}},
    not_found("No such plant"),
    unavailable("The database is unavailable; retry after the given time"),
)


@router.get(
    "/{slug}",
    response_model=PlantProfileResponse,
    response_model_exclude_none=True,
    responses=RESPONSES,
)
def get_plant(slug: str, response: Response, service: Plants) -> PlantProfileResponse:
    """The public plant page; needs no sign-in."""
    profile = PlantProfileResponse.of(service.get(slug))
    response.headers["Cache-Control"] = CACHE_CONTROL
    return profile
