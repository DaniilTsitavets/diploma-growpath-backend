"""Step C of the watering assessment: the recommended interval from the drying rate.

Every observation (days since a full watering, depletion D) bounds the interval I_rec at which
the depletion reaches the target p, under a constant drying rate k = D / t:
  0 < D < p      I_rec = p * t / D, within p * t / (D + e) .. p * t / (D - e)
  D >= p         I_rec <= t
  D < DRY_EPSILON  I_rec > t
The history narrows the corridor by intersecting these intervals and averages k.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.service.watering_depletion import DRY_EPSILON, TOLERANCE
from app.service.watering_types import (
    DepletionSource,
    PotDiameterClass,
    RecBounds,
    WateringObservation,
)

RESET_OBSERVATION = "observation_reset"
RESET_POT_CLASS = "pot_class_changed"
# Categorical observations have no numeric tolerance; they only produce bounds.
DEFAULT_TOLERANCE = TOLERANCE[DepletionSource.DEPTH]


@dataclass(frozen=True)
class Sample:
    days: int
    depletion: float
    tolerance: float


@dataclass(frozen=True)
class Calibrated:
    point_days: float | None
    bounds: RecBounds | None
    n_used: int
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Interval:
    low: float
    high: float | None
    rate: float | None


def usable_history(
    history: Sequence[WateringObservation], pot_class: PotDiameterClass | None
) -> tuple[list[Sample], str | None]:
    """Observations after the last reset or pot change, without partial or invalid ones."""
    start, reason = 0, None
    for position, observation in enumerate(history):
        if observation.reset:
            start, reason = position + 1, RESET_OBSERVATION
        elif _other_pot(observation.pot_diameter_class, pot_class):
            start, reason = position + 1, RESET_POT_CLASS
    samples = [
        Sample(
            observation.days_since_watering,
            observation.depletion,
            TOLERANCE.get(observation.depletion_source, DEFAULT_TOLERANCE),
        )
        for observation in history[start:]
        if not observation.partial
        and observation.days_since_watering > 0
        and 0 <= observation.depletion <= 1
    ]
    return samples, reason


def calibrate(target_p: float, samples: Sequence[Sample]) -> Calibrated:
    """The newest sample is last; samples that contradict each other are cut to the newest."""
    if not samples:
        return Calibrated(None, None, 0)
    used, notes = list(samples), ()
    merged = _merge(target_p, used)
    if merged is None:
        used = used[-1:]
        merged = _merge(target_p, used)
        notes = ("Earlier observations contradict the latest one, so only the latest is used.",)
    if merged is None:
        return Calibrated(None, None, 0, notes)
    low, high, rates = merged
    point = None
    if rates:
        point = target_p / (sum(rates) / len(rates))
        point = max(point, low)
        point = point if high is None else min(point, high)
    bounds = RecBounds(
        lower_days=round(low, 2) if low > 0 else None,
        upper_days=None if high is None else round(high, 2),
    )
    return Calibrated(None if point is None else round(point, 2), bounds, len(used), notes)


def _merge(
    target_p: float, samples: Sequence[Sample]
) -> tuple[float, float | None, list[float]] | None:
    intervals = [_interval(target_p, sample) for sample in samples]
    low = max(interval.low for interval in intervals)
    highs = [interval.high for interval in intervals if interval.high is not None]
    high = min(highs) if highs else None
    if high is not None and low > high:
        return None
    rates = [interval.rate for interval in intervals if interval.rate is not None]
    return low, high, rates


def _interval(target_p: float, sample: Sample) -> _Interval:
    days, depletion = sample.days, sample.depletion
    if depletion < DRY_EPSILON:
        return _Interval(low=float(days), high=None, rate=None)
    if depletion >= target_p:
        return _Interval(low=0.0, high=float(days), rate=None)
    low = target_p * days / (depletion + sample.tolerance)
    spread = depletion - sample.tolerance
    high = target_p * days / spread if spread > 0 else None
    return _Interval(low=low, high=high, rate=depletion / days)


def _other_pot(observed: PotDiameterClass | None, current: PotDiameterClass | None) -> bool:
    unknown = (None, PotDiameterClass.UNKNOWN)
    return observed not in unknown and current not in unknown and observed != current
