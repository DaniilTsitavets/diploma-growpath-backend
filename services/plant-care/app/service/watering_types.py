from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from app.service.plant_profile_model import PlantProfile


class WateringMethod(StrEnum):
    THROUGH_DRAINAGE = "through_drainage"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class PotDiameterClass(StrEnum):
    LT_10 = "lt_10"
    FROM_10_TO_15 = "10_15"
    FROM_15_TO_20 = "15_20"
    FROM_20_TO_30 = "20_30"
    GT_30 = "gt_30"
    UNKNOWN = "unknown"


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class DryDepth(StrEnum):
    NOT_DRY = "not_dry"
    ABOUT_2_3_CM = "about_2_3cm"
    ABOUT_5_CM = "about_5cm"
    WHOLE_ROOT_BALL_DRY = "whole_root_ball_dry"
    UNKNOWN = "unknown"


class PotWeightRelative(StrEnum):
    ABOUT_SAME = "about_same"
    NOTICEABLY_LIGHTER = "noticeably_lighter"
    VERY_LIGHT = "very_light"
    UNKNOWN = "unknown"


class SensorUnit(StrEnum):
    VWC_PCT = "vwc_pct"
    RAW = "raw"


class Light(StrEnum):
    """Ordinal: low < medium < bright_indirect < direct_sun."""

    LOW = "low"
    MEDIUM = "medium"
    BRIGHT_INDIRECT = "bright_indirect"
    DIRECT_SUN = "direct_sun"

    @property
    def rank(self) -> int:
        return list(Light).index(self)


class DroughtTolerance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SoilDrainage(StrEnum):
    FAST = "fast"
    GOOD = "good"


class DepletionSource(StrEnum):
    SENSOR = "sensor"
    WEIGHT_GRAMS = "weight_grams"
    DEPTH = "depth"
    CATEGORICAL_WEIGHT = "categorical_weight"
    NONE = "none"


class PSource(StrEnum):
    PROFILE = "profile"
    PROFILE_CLAMPED = "profile_clamped"
    DEFAULT = "default"


class Status(StrEnum):
    UNDERWATERING = "underwatering"
    OVERWATERING = "overwatering"
    ON_TRACK = "on_track"
    UNCLEAR = "unclear"


class Reliability(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Flag(StrEnum):
    DRAINAGE_NONE = "drainage_none"
    LOW_LIGHT_VS_SPECIES = "low_light_vs_species"
    RH_BELOW_SPECIES_RANGE = "rh_below_species_range"
    TEMP_OUTSIDE_SPECIES_RANGE = "temp_outside_species_range"
    TOP_DRY_HEAVY_CONFLICT = "top_dry_heavy_conflict"


@dataclass(frozen=True)
class WateringInputs:
    """Answers of one update; every answer is optional, `today` is the day of the assessment."""

    today: date
    last_watered_on: date | None = None
    watering_method: WateringMethod | None = None
    pot_diameter_class: PotDiameterClass | None = None
    drainage_holes: Answer | None = None
    standing_water_over_1h: Answer | None = None
    dry_depth: DryDepth | None = None
    pot_weight_relative: PotWeightRelative | None = None
    pot_weight_after_watering_g: float | None = None
    pot_weight_now_g: float | None = None
    lighting: Light | None = None
    repotted_or_moved: bool | None = None
    sensor_reading: float | None = None
    sensor_unit: SensorUnit | None = None
    air_temp_c: float | None = None
    air_rh_pct: float | None = None
    season_changed: bool = False
    reset_reason: str | None = None


@dataclass(frozen=True)
class WateringProfile:
    """The watering facts of the plant species; a missing one is None."""

    dryness_threshold_pct: int | None = None
    drought_tolerance: DroughtTolerance | None = None
    dormancy_shift: bool | None = None
    soil_drainage: SoilDrainage | None = None
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    rh_min_pct: float | None = None
    rh_max_pct: float | None = None
    light_need: Light | None = None

    @classmethod
    def of(cls, profile: PlantProfile) -> WateringProfile:
        return cls(
            dryness_threshold_pct=profile.dryness_threshold_pct,
            drought_tolerance=_member(DroughtTolerance, profile.drought_tolerance),
            dormancy_shift=profile.dormancy_shift,
            soil_drainage=_member(SoilDrainage, profile.soil_drainage),
            temp_min_c=profile.temp_min_c,
            temp_max_c=profile.temp_max_c,
            rh_min_pct=profile.rh_min_pct,
            rh_max_pct=profile.rh_max_pct,
            light_need=_member(Light, profile.light_need),
        )


@dataclass(frozen=True)
class WateringObservation:
    """One earlier assessment of the same plant; the history is ordered from oldest to newest.

    A categorical observation stores depletion 0.0 (wet) or 1.0 (at or past the threshold).
    `reset` marks an event (repotting, a moved pot, a season change) after which the observations
    up to and including this one no longer describe the pot.
    """

    days_since_watering: int
    depletion: float
    depletion_source: DepletionSource = DepletionSource.DEPTH
    partial: bool = False
    reset: bool = False
    pot_diameter_class: PotDiameterClass | None = None


@dataclass(frozen=True)
class RecBounds:
    """The recommended interval lies above `lower_days` and does not exceed `upper_days`."""

    lower_days: float | None = None
    upper_days: float | None = None

    def describe(self) -> str | None:
        if self.lower_days is not None and self.upper_days is not None:
            return f"between {self.lower_days:g} and {self.upper_days:g} days"
        if self.lower_days is not None:
            return f"more than {self.lower_days:g} days"
        if self.upper_days is not None:
            return f"no more than {self.upper_days:g} days"
        return None


@dataclass(frozen=True)
class AirContext:
    """Context only: the vapour pressure deficit never scales an interval."""

    temperature_c: float | None = None
    rh_pct: float | None = None
    vpd_kpa: float | None = None


@dataclass(frozen=True)
class Calibration:
    n_observations: int
    reset_reason: str | None = None


@dataclass(frozen=True)
class WateringAssessment:
    depletion_d: float | None
    d_source: DepletionSource
    target_p: float
    p_source: PSource
    i_rec_days: float | None
    i_rec_bounds: RecBounds | None
    i_fact_days: int | None
    ratio_r: float | None
    ks: float | None
    status: Status
    flags: tuple[Flag, ...]
    air: AirContext
    calibration: Calibration
    assumptions: tuple[str, ...]
    reliability: Reliability

    def to_dict(self) -> dict[str, Any]:
        bounds = self.i_rec_bounds
        return {
            "depletion_D": self.depletion_d,
            "D_source": self.d_source.value,
            "target_p": self.target_p,
            "p_source": self.p_source.value,
            "I_rec_days": self.i_rec_days,
            "I_rec_bounds": None
            if bounds is None
            else {
                "lower_days": bounds.lower_days,
                "upper_days": bounds.upper_days,
                "text": bounds.describe(),
            },
            "I_fact_days": self.i_fact_days,
            "ratio_r": self.ratio_r,
            "Ks": self.ks,
            "status": self.status.value,
            "flags": [flag.value for flag in self.flags],
            "air": {
                "T": self.air.temperature_c,
                "RH": self.air.rh_pct,
                "VPD_kPa": self.air.vpd_kpa,
            },
            "calibration": {
                "n_observations": self.calibration.n_observations,
                "reset_reason": self.calibration.reset_reason,
            },
            "assumptions": list(self.assumptions),
            "reliability": self.reliability.value,
        }


def _member[E: StrEnum](enum: type[E], value: str | None) -> E | None:
    return None if value is None else enum(value)
