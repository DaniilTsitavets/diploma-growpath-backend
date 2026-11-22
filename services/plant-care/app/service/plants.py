import re
from collections.abc import Callable

from app.service.errors import PlantNotFound
from app.service.plant_profile_model import PlantProfile
from app.service.ports import UnitOfWork

PLANT_SLUG = re.compile(r"^[a-z0-9-]{1,64}$")


class PlantService:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def get(self, slug: str) -> PlantProfile:
        if not PLANT_SLUG.fullmatch(slug):
            raise PlantNotFound
        with self._unit_of_work() as uow:
            profile = uow.plant_profiles.get(slug)
        if profile is None:
            raise PlantNotFound
        return profile
