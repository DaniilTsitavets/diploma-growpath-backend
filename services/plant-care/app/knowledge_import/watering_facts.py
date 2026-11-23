"""Watering facts of a PlantSolve record; a value that cannot be read reliably is None."""

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

DRYNESS_THRESHOLDS = (25, 50, 100)
DROUGHT_TOLERANCES = ("low", "medium", "high")
SOIL_DRAINAGES = ("fast", "good")
LIGHT_NEEDS = ("low", "medium", "bright_indirect", "direct_sun")
# lightMin is the dimmest light the plant is documented to live in.
LIGHT_NEED_OF_LIGHT_MIN = {
    "low": "low",
    "medium": "medium",
    "bright": "bright_indirect",
    "full": "direct_sun",
}

MIN_TEMP_C, MAX_TEMP_C = -50.0, 60.0

_NUMBER = r"-?\d+(?:\.\d+)?"
_SEPARATOR = r"(?:-|–|—|\bto\b)"
_TEMP_RANGE = re.compile(rf"({_NUMBER})\s*(?:°\s*[CF])?\s*{_SEPARATOR}\s*({_NUMBER})\s*°\s*([CF])")
_RH_RANGE = re.compile(rf"(\d{{1,3}})\s*%?\s*{_SEPARATOR}\s*(\d{{1,3}})\s*%(\+)?")

Range = tuple[float | None, float | None]
NO_RANGE: Range = (None, None)


def parse_temperature_range(text: object) -> Range:
    """Degrees Celsius; a Fahrenheit-only range is converted, an ambiguous text is None."""
    if not isinstance(text, str):
        return NO_RANGE
    celsius: set[tuple[float, float]] = set()
    fahrenheit: set[tuple[float, float]] = set()
    for low, high, unit in _TEMP_RANGE.findall(text):
        (celsius if unit == "C" else fahrenheit).add((float(low), float(high)))
    if len(celsius) == 1:
        low, high = next(iter(celsius))
    elif not celsius and len(fahrenheit) == 1:
        low, high = (_to_celsius(value) for value in next(iter(fahrenheit)))
    else:
        return NO_RANGE
    if not MIN_TEMP_C <= low <= high <= MAX_TEMP_C:
        return NO_RANGE
    return low, high


def parse_humidity_range(text: object) -> Range:
    """Percent bounds; "50-60%+" has no upper bound; words and single values are None."""
    if not isinstance(text, str):
        return NO_RANGE
    matches = set(_RH_RANGE.findall(text))
    if len(matches) != 1:
        return NO_RANGE
    low, high, open_ended = next(iter(matches))
    if not 0 <= int(low) <= int(high) <= 100:
        return NO_RANGE
    return float(low), None if open_ended else float(high)


def first_range(parameter: object, hero_text: object, parse: Callable[[object], Range]) -> Range:
    """The recommended value of `parameters.*` first, then the `plantHero` text."""
    recommended = parameter.get("recommended") if isinstance(parameter, Mapping) else None
    for text in (recommended, hero_text):
        found = parse(text)
        if found != NO_RANGE:
            return found
    return NO_RANGE


def watering_facts(record: Mapping[str, Any]) -> dict[str, Any]:
    """The profile columns derived from `plantHero` and `parameters`."""
    hero = _mapping(record.get("plantHero"))
    parameters = _mapping(record.get("parameters"))
    temp_min, temp_max = first_range(
        parameters.get("temperature"), hero.get("temperature"), parse_temperature_range
    )
    rh_min, rh_max = first_range(
        parameters.get("humidity"), hero.get("humidity"), parse_humidity_range
    )
    return {
        "dryness_threshold_pct": _member(hero.get("drynessThreshold"), DRYNESS_THRESHOLDS),
        "drought_tolerance": _member(_word(hero.get("droughtTolerance")), DROUGHT_TOLERANCES),
        "dormancy_shift": _flag(hero.get("dormancyShift")),
        "soil_drainage": _member(_word(hero.get("soilDrainage")), SOIL_DRAINAGES),
        "temp_min_c": temp_min,
        "temp_max_c": temp_max,
        "rh_min_pct": None if rh_min is None else int(rh_min),
        "rh_max_pct": None if rh_max is None else int(rh_max),
        "light_need": LIGHT_NEED_OF_LIGHT_MIN.get(_word(hero.get("lightMin")) or ""),
    }


def _to_celsius(fahrenheit: float) -> float:
    return round((fahrenheit - 32) * 5 / 9, 1)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _word(value: object) -> str | None:
    return value.strip().lower() if isinstance(value, str) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _member(value: object, allowed: Sequence[Any]) -> Any:
    return value if isinstance(value, type(allowed[0])) and value in allowed else None
