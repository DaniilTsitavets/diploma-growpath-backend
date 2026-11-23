"""Step A of the watering assessment: how much of the readily available water is gone."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.service.watering_types import (
    DepletionSource,
    DryDepth,
    PotDiameterClass,
    PotWeightRelative,
    SensorUnit,
    WateringInputs,
)

# Peat-based substrates; the author's choice.
THETA_FLOOR = 0.20
# Unverified assumption: volumetric water content of a freely drained potting mix.
THETA_FULL = 0.45
# Unverified assumption: at depletion 1 the pot has lost 25% of its weight after watering
# (water about 45% of the full pot weight, of which the part above THETA_FLOOR is lost).
WEIGHT_DRY_REF_FRACTION = 0.75
# Height of the root zone: the midpoint of the pot diameter class, in cm (the author's assumption).
ROOT_ZONE_CM = {
    PotDiameterClass.LT_10: 8.0,
    PotDiameterClass.FROM_10_TO_15: 12.5,
    PotDiameterClass.FROM_15_TO_20: 17.5,
    PotDiameterClass.FROM_20_TO_30: 25.0,
    PotDiameterClass.GT_30: 35.0,
}
LARGE_POTS = (PotDiameterClass.FROM_20_TO_30, PotDiameterClass.GT_30)
# Depth of the dry layer for the answers with a number (extension guides, as cited by the author).
DRY_DEPTH_CM = {DryDepth.NOT_DRY: 0.0, DryDepth.ABOUT_2_3_CM: 2.5, DryDepth.ABOUT_5_CM: 5.0}
DEEP_DRY = (DryDepth.ABOUT_5_CM, DryDepth.WHOLE_ROOT_BALL_DRY)
# Unverified assumptions: the half-width of the error of D per source.
TOLERANCE = {
    DepletionSource.SENSOR: 0.05,
    DepletionSource.WEIGHT_GRAMS: 0.10,
    DepletionSource.DEPTH: 0.15,
}
DRY_EPSILON = 0.05


class Level(StrEnum):
    """What a categorical answer says about the depletion, without a number."""

    WET = "wet"
    AT_THRESHOLD = "at_threshold"
    VERY_DRY = "very_dry"


@dataclass(frozen=True)
class Depletion:
    value: float | None
    source: DepletionSource
    tolerance: float | None = None
    level: Level | None = None
    top_dry_heavy_conflict: bool = False
    assumptions: tuple[str, ...] = ()


def estimate_depletion(inputs: WateringInputs) -> Depletion:
    """The first usable signal wins: sensor, grams, depth, categorical weight."""
    notes = _raw_sensor_note(inputs)
    sensor = _sensor(inputs)
    grams = _grams(inputs)
    depth_value = _depth_value(inputs)
    weight_level = _weight_level(inputs.pot_weight_relative)
    depth_level = _depth_level(inputs.dry_depth)

    weight_wet = (grams is not None and grams < DRY_EPSILON) or weight_level is Level.WET
    conflict = inputs.dry_depth in DEEP_DRY and weight_wet
    weight_answered = inputs.pot_weight_relative in (
        PotWeightRelative.ABOUT_SAME,
        PotWeightRelative.NOTICEABLY_LIGHTER,
        PotWeightRelative.VERY_LIGHT,
    )

    if sensor is not None:
        chosen = _numeric(sensor, DepletionSource.SENSOR)
    elif grams is not None:
        chosen = _numeric(grams, DepletionSource.WEIGHT_GRAMS)
    elif weight_answered and (inputs.pot_diameter_class in LARGE_POTS or conflict):
        chosen = _categorical(DepletionSource.CATEGORICAL_WEIGHT, weight_level)
    elif depth_value is not None:
        chosen = _numeric(depth_value, DepletionSource.DEPTH)
    elif depth_level is not None:
        chosen = _categorical(DepletionSource.DEPTH, depth_level)
    elif weight_level is not None:
        chosen = _categorical(DepletionSource.CATEGORICAL_WEIGHT, weight_level)
    elif weight_answered:
        chosen = _categorical(DepletionSource.CATEGORICAL_WEIGHT, None)
    else:
        chosen = Depletion(None, DepletionSource.NONE)
    return Depletion(
        chosen.value,
        chosen.source,
        chosen.tolerance,
        chosen.level,
        conflict,
        notes + chosen.assumptions,
    )


def _numeric(value: float, source: DepletionSource) -> Depletion:
    return Depletion(round(value, 2), source, TOLERANCE[source], assumptions=_NOTES[source])


def _categorical(source: DepletionSource, level: Level | None) -> Depletion:
    return Depletion(
        None, source, level=level, assumptions=_NOTES[DepletionSource.CATEGORICAL_WEIGHT]
    )


def _sensor(inputs: WateringInputs) -> float | None:
    reading = inputs.sensor_reading
    if inputs.sensor_unit is not SensorUnit.VWC_PCT or reading is None:
        return None
    if not 0 <= reading <= 100:
        return None
    return _clamp((THETA_FULL - reading / 100) / (THETA_FULL - THETA_FLOOR))


def _grams(inputs: WateringInputs) -> float | None:
    full, now = inputs.pot_weight_after_watering_g, inputs.pot_weight_now_g
    if full is None or now is None or full <= 0 or now <= 0:
        return None
    return _clamp((full - now) / (full * (1 - WEIGHT_DRY_REF_FRACTION)))


def _depth_value(inputs: WateringInputs) -> float | None:
    height = ROOT_ZONE_CM.get(inputs.pot_diameter_class) if inputs.pot_diameter_class else None
    if height is None or inputs.dry_depth is None:
        return None
    if inputs.dry_depth is DryDepth.WHOLE_ROOT_BALL_DRY:
        return 1.0
    depth_cm = DRY_DEPTH_CM.get(inputs.dry_depth)
    return None if depth_cm is None else _clamp(depth_cm / height)


def _weight_level(relative: PotWeightRelative | None) -> Level | None:
    return (
        {
            PotWeightRelative.ABOUT_SAME: Level.WET,
            PotWeightRelative.VERY_LIGHT: Level.AT_THRESHOLD,
        }.get(relative)
        if relative
        else None
    )


def _depth_level(depth: DryDepth | None) -> Level | None:
    return (
        {
            DryDepth.NOT_DRY: Level.WET,
            DryDepth.WHOLE_ROOT_BALL_DRY: Level.VERY_DRY,
        }.get(depth)
        if depth
        else None
    )


def _raw_sensor_note(inputs: WateringInputs) -> tuple[str, ...]:
    if inputs.sensor_reading is None or inputs.sensor_unit is SensorUnit.VWC_PCT:
        return ()
    return (
        "The sensor reading has no unit vwc_pct, so it gives a trend only;"
        " no numeric depletion is computed from it.",
    )


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


_NOTES = {
    DepletionSource.SENSOR: (
        f"Depletion from the soil sensor assumes {THETA_FULL:g} at full and {THETA_FLOOR:g}"
        " at the floor (volumetric water content, peat substrate).",
    ),
    DepletionSource.WEIGHT_GRAMS: (
        f"Depletion from the weight assumes the pot weighs {WEIGHT_DRY_REF_FRACTION:.0%}"
        " of its weight after watering when the root zone is dry.",
    ),
    DepletionSource.DEPTH: (
        "Depletion from the dry depth assumes the root zone is as high as the middle of"
        " the pot diameter class.",
    ),
    DepletionSource.CATEGORICAL_WEIGHT: (
        "The answers are categories without a pot size, so they give bounds, not a number.",
    ),
}
