"""Pure watering assessment of one plant update: no I/O, no model call.

Depletion D is the share of the readily available water that is gone (0 = just watered, 1 = the
root zone is at the floor). The target p is the depletion at which the plant should be watered.
The method follows the FAO Irrigation and Drainage Paper 56 (see docs/watering.md for each
constant and assumption).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

from app.service.watering_calibration import DEFAULT_TOLERANCE, Sample, calibrate, usable_history
from app.service.watering_depletion import Depletion, Level, estimate_depletion
from app.service.watering_types import (
    AirContext,
    Answer,
    Calibration,
    DepletionSource,
    Flag,
    PSource,
    RecBounds,
    Reliability,
    Status,
    WateringAssessment,
    WateringInputs,
    WateringMethod,
    WateringObservation,
    WateringProfile,
)

# FAO-56 range of the allowable depletion fraction, as cited by the author.
P_MIN, P_MAX = 0.1, 0.8
DEFAULT_P = 0.5
# The author's threshold: a pot that is still this wet when it is watered again is overwatered.
OVERWATERED_DEPLETION = 0.1
# Unverified assumption: this many consistent observations raise the reliability one level.
HISTORY_RELIABILITY_MIN = 3

REASON_REPOTTED = "repotted_or_moved"
REASON_SEASON = "season_change"

__all__ = [
    "DEFAULT_P",
    "HISTORY_RELIABILITY_MIN",
    "OVERWATERED_DEPLETION",
    "P_MAX",
    "P_MIN",
    "assess_watering",
    "saturation_vapour_pressure_kpa",
    "vapour_pressure_deficit_kpa",
]


def assess_watering(
    inputs: WateringInputs,
    profile: WateringProfile | None = None,
    history: Sequence[WateringObservation] = (),
    *,
    overwatered_depletion: float = OVERWATERED_DEPLETION,
) -> WateringAssessment:
    profile = profile or WateringProfile()
    target_p, p_source = target_depletion(profile)
    depletion = estimate_depletion(inputs)
    days = _days_since(inputs.last_watered_on, inputs.today)
    assumptions = [*_p_assumption(profile, target_p, p_source), *depletion.assumptions]

    reset_reason = _reset_reason(inputs, profile)
    samples, history_reason = (
        ([], None) if reset_reason else usable_history(history, inputs.pot_diameter_class)
    )
    current = _current_sample(depletion, days, inputs.watering_method)
    calibration_open = days is not None and days >= 0
    calibrated = calibrate(
        target_p, [*samples, *([current] if current else [])] if calibration_open else []
    )
    assumptions += calibrated.assumptions
    if calibrated.n_used:
        assumptions.append("The drying rate is assumed constant between waterings.")
    assumptions += _timing_assumptions(inputs, days, current is not None)

    ratio = _ratio(days, calibrated.point_days, depletion.value, target_p)
    ks = _stress_coefficient(ratio, target_p)
    status = _status(days, depletion, ks, overwatered_depletion)
    air = _air(inputs)
    if air.vpd_kpa is not None:
        assumptions.append("VPD is context only and never scales the interval.")
    flags = _flags(inputs, profile, depletion)
    if Flag.TOP_DRY_HEAVY_CONFLICT in flags:
        assumptions.append(
            "The dry depth says dry but the pot is heavy: only the top layer is dry,"
            " so the depletion follows the weight."
        )

    return WateringAssessment(
        depletion_d=depletion.value,
        d_source=depletion.source,
        target_p=target_p,
        p_source=p_source,
        i_rec_days=calibrated.point_days,
        i_rec_bounds=_bounds(calibrated.bounds),
        i_fact_days=days if days is not None and days >= 0 else None,
        ratio_r=None if ratio is None else round(ratio, 2),
        ks=None if ks is None else round(ks, 2),
        status=status,
        flags=flags,
        air=air,
        calibration=Calibration(calibrated.n_used, reset_reason or history_reason),
        assumptions=tuple(assumptions),
        reliability=_reliability(
            depletion, p_source, days, calibrated.n_used, inputs.watering_method
        ),
    )


def target_depletion(profile: WateringProfile) -> tuple[float, PSource]:
    """p is assumed equal to the species dryness threshold, clamped to the FAO-56 range."""
    if profile.dryness_threshold_pct is None:
        return DEFAULT_P, PSource.DEFAULT
    raw = profile.dryness_threshold_pct / 100
    clamped = min(P_MAX, max(P_MIN, raw))
    return clamped, PSource.PROFILE if clamped == raw else PSource.PROFILE_CLAMPED


def saturation_vapour_pressure_kpa(temperature_c: float) -> float:
    """FAO-56 equation 11."""
    return 0.6108 * math.exp(17.27 * temperature_c / (temperature_c + 237.3))


def vapour_pressure_deficit_kpa(temperature_c: float, rh_pct: float) -> float:
    return saturation_vapour_pressure_kpa(temperature_c) * (1 - rh_pct / 100)


def _days_since(last_watered_on: date | None, today: date) -> int | None:
    return None if last_watered_on is None else (today - last_watered_on).days


def _reset_reason(inputs: WateringInputs, profile: WateringProfile) -> str | None:
    if inputs.reset_reason:
        return inputs.reset_reason
    if inputs.repotted_or_moved:
        return REASON_REPOTTED
    if inputs.season_changed and profile.dormancy_shift:
        return REASON_SEASON
    return None


def _current_sample(
    depletion: Depletion, days: int | None, method: WateringMethod | None
) -> Sample | None:
    if days is None or days <= 0 or method is WateringMethod.PARTIAL:
        return None
    if depletion.value is not None and depletion.tolerance is not None:
        return Sample(days, depletion.value, depletion.tolerance)
    if depletion.level is Level.WET:
        return Sample(days, 0.0, DEFAULT_TOLERANCE)
    if depletion.level in (Level.AT_THRESHOLD, Level.VERY_DRY):
        return Sample(days, 1.0, DEFAULT_TOLERANCE)
    return None


def _ratio(
    days: int | None, i_rec: float | None, depletion: float | None, target_p: float
) -> float | None:
    if days is None or days <= 0:
        return None
    if i_rec:
        return days / i_rec
    return None if depletion is None else depletion / target_p


def _stress_coefficient(ratio: float | None, target_p: float) -> float | None:
    """FAO-56 equation 84 written with r = I_fact / I_rec."""
    if ratio is None:
        return None
    if ratio <= 1 or target_p >= 1:
        return 1.0
    return min(1.0, max(0.0, (1 - ratio * target_p) / (1 - target_p)))


def _status(
    days: int | None, depletion: Depletion, ks: float | None, overwatered_depletion: float
) -> Status:
    if days is None or days <= 0:
        return Status.UNCLEAR
    if depletion.level is Level.WET or (
        depletion.value is not None and depletion.value < overwatered_depletion
    ):
        return Status.OVERWATERING
    if depletion.level is Level.VERY_DRY or (ks is not None and ks < 1):
        return Status.UNDERWATERING
    if ks is not None or depletion.level is Level.AT_THRESHOLD:
        return Status.ON_TRACK
    return Status.UNCLEAR


def _flags(
    inputs: WateringInputs, profile: WateringProfile, depletion: Depletion
) -> tuple[Flag, ...]:
    flags = []
    if inputs.drainage_holes is Answer.NO or inputs.standing_water_over_1h is Answer.YES:
        flags.append(Flag.DRAINAGE_NONE)
    if (
        inputs.lighting is not None
        and profile.light_need is not None
        and inputs.lighting.rank < profile.light_need.rank
    ):
        flags.append(Flag.LOW_LIGHT_VS_SPECIES)
    rh = _valid_rh(inputs.air_rh_pct)
    if rh is not None and profile.rh_min_pct is not None and rh < profile.rh_min_pct:
        flags.append(Flag.RH_BELOW_SPECIES_RANGE)
    if _outside(inputs.air_temp_c, profile.temp_min_c, profile.temp_max_c):
        flags.append(Flag.TEMP_OUTSIDE_SPECIES_RANGE)
    if depletion.top_dry_heavy_conflict:
        flags.append(Flag.TOP_DRY_HEAVY_CONFLICT)
    return tuple(flags)


def _outside(value: float | None, low: float | None, high: float | None) -> bool:
    if value is None:
        return False
    return (low is not None and value < low) or (high is not None and value > high)


def _valid_rh(rh_pct: float | None) -> float | None:
    return rh_pct if rh_pct is not None and 0 <= rh_pct <= 100 else None


def _air(inputs: WateringInputs) -> AirContext:
    rh = _valid_rh(inputs.air_rh_pct)
    temperature = inputs.air_temp_c
    vpd = None
    if temperature is not None and rh is not None:
        vpd = round(vapour_pressure_deficit_kpa(temperature, rh), 2)
    return AirContext(temperature, rh, vpd)


def _bounds(bounds: RecBounds | None) -> RecBounds | None:
    if bounds is None or (bounds.lower_days is None and bounds.upper_days is None):
        return None
    return bounds


def _reliability(
    depletion: Depletion,
    p_source: PSource,
    days: int | None,
    n_used: int,
    method: WateringMethod | None,
) -> Reliability:
    ladder = [Reliability.LOW, Reliability.MEDIUM, Reliability.HIGH]
    base = {
        DepletionSource.SENSOR: 2,
        DepletionSource.WEIGHT_GRAMS: 2,
        DepletionSource.DEPTH: 1 if depletion.value is not None else 0,
    }.get(depletion.source, 0)
    level = min(2, base + (1 if n_used >= HISTORY_RELIABILITY_MIN else 0))
    if p_source is PSource.DEFAULT:
        level = min(level, 1)
    if days is None or days <= 0:
        level = 0
    elif method is WateringMethod.PARTIAL:
        level = min(level, 1)
    return ladder[level]


def _p_assumption(profile: WateringProfile, target_p: float, source: PSource) -> list[str]:
    if source is PSource.DEFAULT:
        return [
            f"No species dryness threshold is known, so the target depletion p is {target_p:g}."
        ]
    note = (
        f"The target depletion p is assumed to equal the species dryness threshold"
        f" ({profile.dryness_threshold_pct}%)"
    )
    if source is PSource.PROFILE_CLAMPED:
        return [f"{note} and is clamped to {target_p:g}, inside {P_MIN:g}..{P_MAX:g}."]
    return [f"{note}."]


def _timing_assumptions(inputs: WateringInputs, days: int | None, usable: bool) -> list[str]:
    notes = [
        "The status describes the interval that ends today and assumes the pot is watered now."
    ]
    if days is None:
        notes.append("There is no last watering date: only the flags and the depletion are given.")
    elif days < 0:
        notes.append("The last watering date is after today, so the timing is ignored.")
    elif days == 0:
        notes.append("The pot was watered today, so no interval can be judged.")
    elif inputs.watering_method is WateringMethod.PARTIAL:
        notes.append("The last watering was partial, so it is not used for calibration.")
    elif usable:
        notes.append("The last watering is counted as a full one.")
    return notes
