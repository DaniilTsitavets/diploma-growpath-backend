"""Maps stored updates to the inputs and the history of the pure watering assessment."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date
from decimal import Decimal

from app.service import watering_types as wt
from app.service.model import UpdateRecord, YesNo
from app.service.watering_depletion import Depletion, Level, estimate_depletion


def watering_inputs(update: UpdateRecord, today: date) -> wt.WateringInputs:
    return wt.WateringInputs(
        today=today,
        last_watered_on=update.last_watered_on,
        watering_method=wt.WateringMethod(update.watering_method.value),
        pot_diameter_class=wt.PotDiameterClass(update.pot_diameter_class.value),
        drainage_holes=wt.Answer(update.drainage_holes.value),
        standing_water_over_1h=wt.Answer(update.standing_water_over_1h.value),
        dry_depth=wt.DryDepth(update.dry_depth.value),
        pot_weight_relative=wt.PotWeightRelative(update.pot_weight_relative.value),
        pot_weight_after_watering_g=_number(update.pot_weight_after_watering_g),
        pot_weight_now_g=_number(update.pot_weight_now_g),
        lighting=wt.Light(update.lighting.value),
        repotted_or_moved=_repotted(update.repotted_or_moved),
        sensor_reading=_number(update.sensor_reading),
        sensor_unit=None if update.sensor_unit is None else wt.SensorUnit(update.sensor_unit.value),
        air_temp_c=_number(update.air_temp_c),
        air_rh_pct=_number(update.air_rh_pct),
    )


def watering_history(updates_newest_first: Sequence[UpdateRecord]) -> list[wt.WateringObservation]:
    """Observations of the earlier updates, oldest first, as the assessment expects them.

    An update that reports a repotting or a move cuts the observations before it, so the one
    just before it is marked as a reset.
    """
    history: list[wt.WateringObservation] = []
    for update in reversed(updates_newest_first):
        if update.repotted_or_moved is YesNo.YES and history:
            history[-1] = replace(history[-1], reset=True)
        observation = _observation(update)
        if observation is not None:
            history.append(observation)
    return history


def _observation(update: UpdateRecord) -> wt.WateringObservation | None:
    """The depletion an update showed on its own day; None when it cannot be told or dated."""
    if update.last_watered_on is None:
        return None
    day = update.created_at.astimezone(UTC).date()
    inputs = watering_inputs(update, day)
    depletion = estimate_depletion(inputs)
    value = _depletion_value(depletion)
    if value is None:
        return None
    return wt.WateringObservation(
        days_since_watering=(day - update.last_watered_on).days,
        depletion=value,
        depletion_source=depletion.source,
        partial=inputs.watering_method is wt.WateringMethod.PARTIAL,
        pot_diameter_class=inputs.pot_diameter_class,
    )


def _depletion_value(depletion: Depletion) -> float | None:
    """A categorical answer becomes 0.0 (wet) or 1.0 (at or past the target)."""
    if depletion.value is not None:
        return depletion.value
    if depletion.level is None:
        return None
    return 0.0 if depletion.level is Level.WET else 1.0


def _repotted(answer: YesNo | None) -> bool | None:
    return None if answer is None else answer is YesNo.YES


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
