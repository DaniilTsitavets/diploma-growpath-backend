"""Catalog labels derived from a PlantSolve record; the rules are in docs/catalog-labels.md."""

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DIFFICULTIES = {"beginner", "intermediate", "advanced"}
LOW_MAINTENANCE_TOLERANCES = {"medium", "high"}
HUMIDITY_LOVER_MIN_PCT = 60
HUMIDITY_LOVER_MAX_PCT = 80
COMPACT_MAX_CM = 65
LARGE_MIN_CM = 180
PET_SAFETY_LABELS = {"safe": "pet-safe", "toxic": "toxic-to-pets"}
# Only tags that add to the profile facts; the rest ("indoor-plants", "vastu") is noise.
TAXONOMY_LABELS = {
    "air-purifying": "air-purifying",
    "pollinator-friendly": "pollinator-friendly",
    "fragrant": "fragrant",
    "succulent": "succulent",
    "herbs": "herb",
    "edible-plants": "edible",
    "hanging-plants": "hanging",
    "trailing-vine": "hanging",
}

_CM_PER_UNIT = {"cm": 1.0, "in": 2.54, "inch": 2.54, "inches": 2.54, "ft": 30.48, "feet": 30.48}
_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(\d+(?:\.\d+)?))?\s*(cm|inches|inch|in|feet|ft)\b",
    re.IGNORECASE,
)
_TAG_SEPARATORS = re.compile(r"[\s_]+")


def derive_labels(record: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[str, ...]:
    """Sorted labels for one plant; `facts` are the profile columns of `watering_facts`."""
    hero = _mapping(record.get("plantHero"))
    labels = {
        _difficulty(hero),
        _low_maintenance(hero, facts),
        *_light(hero, facts),
        *_watering(facts),
        _pets(hero, record),
        _humidity(facts),
        "flowering" if hero.get("flowering") is True else None,
        _size(hero),
        *_taxonomy(record),
    }
    return tuple(sorted(label for label in labels if label))


def write_label_table(path: Path, table: Mapping[str, Sequence[str]]) -> None:
    """The file commerce reads: plant slug to labels; sorted keys keep a rerun byte-identical."""
    ordered = {slug: list(table[slug]) for slug in sorted(table)}
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def _difficulty(hero: Mapping[str, Any]) -> str | None:
    value = _word(hero.get("difficulty"))
    return value if value in DIFFICULTIES else None


def _low_maintenance(hero: Mapping[str, Any], facts: Mapping[str, Any]) -> str | None:
    easy = _difficulty(hero) == "beginner"
    return (
        "low-maintenance"
        if easy and facts["drought_tolerance"] in LOW_MAINTENANCE_TOLERANCES
        else None
    )


def _light(hero: Mapping[str, Any], facts: Mapping[str, Any]) -> list[str]:
    need = facts["light_need"]
    labels = []
    if need == "low":
        labels.append("low-light")
    if need in {"bright_indirect", "direct_sun"}:
        labels.append("bright-light")
    if need == "direct_sun" or (_word(hero.get("light")) or "").startswith("full sun"):
        labels.append("direct-sun")
    return labels


def _watering(facts: Mapping[str, Any]) -> list[str]:
    if facts["drought_tolerance"] == "high":
        return ["drought-tolerant"]
    if facts["drought_tolerance"] == "low" and facts["dryness_threshold_pct"] == 25:
        return ["likes-moisture"]
    return []


def _pets(hero: Mapping[str, Any], record: Mapping[str, Any]) -> str | None:
    label = PET_SAFETY_LABELS.get(_word(hero.get("petSafety")) or "")
    if label:
        return label
    pet_safe = _mapping(record.get("petSafe"))
    cat, dog = pet_safe.get("cat"), pet_safe.get("dog")
    if cat is True and dog is True:
        return "pet-safe"
    if cat is False or dog is False:
        return "toxic-to-pets"
    return None


def _humidity(facts: Mapping[str, Any]) -> str | None:
    low, high = facts["rh_min_pct"], facts["rh_max_pct"]
    loves = (low is not None and low >= HUMIDITY_LOVER_MIN_PCT) or (
        high is not None and high >= HUMIDITY_LOVER_MAX_PCT
    )
    return "humidity-lover" if loves else None


def _size(hero: Mapping[str, Any]) -> str | None:
    text = hero.get("matureSize")
    found = _SIZE.search(text) if isinstance(text, str) else None
    if found is None:
        return None
    top_cm = float(found.group(2) or found.group(1)) * _CM_PER_UNIT[found.group(3).lower()]
    if top_cm <= COMPACT_MAX_CM:
        return "compact"
    return "large" if top_cm >= LARGE_MIN_CM else None


def _taxonomy(record: Mapping[str, Any]) -> list[str]:
    tags = record.get("taxonomyTags")
    if not isinstance(tags, list):
        return []
    normalized = (
        _TAG_SEPARATORS.sub("-", tag.strip().lower()) for tag in tags if isinstance(tag, str)
    )
    return [TAXONOMY_LABELS[tag] for tag in normalized if tag in TAXONOMY_LABELS]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _word(value: object) -> str | None:
    return value.strip().lower() if isinstance(value, str) else None
