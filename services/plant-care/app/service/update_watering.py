from datetime import date

from app.service.model import UpdateRecord
from app.service.ports import PlantProfileRepository, UpdateRepository
from app.service.watering import assess_watering
from app.service.watering_input import watering_history, watering_inputs
from app.service.watering_types import WateringAssessment, WateringProfile


def assess_update(
    profiles: PlantProfileRepository,
    updates: UpdateRepository,
    update: UpdateRecord,
    species: str,
    today: date,
    history_limit: int,
) -> WateringAssessment:
    """Assess the update against its species profile and the earlier updates of the journal.

    A species without a profile is assessed without species facts. Raises Unavailable when
    the database cannot be reached.
    """
    profile = profiles.get_by_species(species)
    earlier = updates.earlier_in_journal(update, history_limit) if history_limit > 0 else []
    return assess_watering(
        watering_inputs(update, today),
        None if profile is None else WateringProfile.of(profile),
        watering_history(earlier),
    )
