from datetime import date

from app.service.model import UpdateRecord
from app.service.ports import PlantProfileRepository, UpdateRepository
from app.service.watering import assess_watering
from app.service.watering_input import crossed_season, watering_history, watering_inputs
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
    watering_profile = None if profile is None else WateringProfile.of(profile)
    dormancy_shift = watering_profile is not None and watering_profile.dormancy_shift is True
    return assess_watering(
        watering_inputs(
            update,
            today,
            season_changed=bool(earlier) and crossed_season(earlier[0].created_at, today),
        ),
        watering_profile,
        watering_history(earlier, reset_on_season_change=dormancy_shift),
    )
