from datetime import date

import pytest

from app.knowledge_import.convert import convert_profile
from app.knowledge_import.plants import PlantEntry
from app.knowledge_import.watering_facts import (
    parse_humidity_range,
    parse_temperature_range,
    watering_facts,
)
from tests.unit.plantsolve_data import plant_record

ENTRY = PlantEntry("lavender", "lavender", "Lavender")
HERO = {
    "drynessThreshold": 100,
    "droughtTolerance": "high",
    "dormancyShift": True,
    "soilDrainage": "fast",
    "lightMin": "bright",
    "temperature": "65-85°F (18-29°C)",
    "humidity": "20-40%",
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("65-85°F (18-29°C)", (18.0, 29.0)),
        ("16°C to 24°C (60°F to 75°F)", (16.0, 24.0)),
        ("18-24°C (65-75°F)", (18.0, 24.0)),
        ("65°F - 80°F (18°C - 27°C)", (18.0, 27.0)),
        ("65°F - 80°F", (18.3, 26.7)),
        ("60-80°F", (15.6, 26.7)),
        ("10-95°F (-12-35°C) depending on species", (-12.0, 35.0)),
        ("18-24°C", (18.0, 24.0)),
        ("warm", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
        ("18-24 degrees", (None, None)),
        ("65-75°F in summer, 55-65°F in winter", (None, None)),
        ("24-18°C", (None, None)),
        ("100-200°C", (None, None)),
    ],
)
def test_temperature_ranges_are_parsed_in_celsius_or_not_at_all(text, expected):
    assert parse_temperature_range(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("40-60%", (40.0, 60.0)),
        ("60% - 80%", (60.0, 80.0)),
        ("50% to 80%", (50.0, 80.0)),
        ("50-60%+", (50.0, None)),
        ("low-medium", (None, None)),
        ("Medium to high", (None, None)),
        ("low", (None, None)),
        ("recommended 30%", (None, None)),
        ("80-60%", (None, None)),
        ("50-150%", (None, None)),
        ("30-40% or 60-70%", (None, None)),
        (None, (None, None)),
    ],
)
def test_humidity_ranges_are_parsed_in_percent_or_not_at_all(text, expected):
    assert parse_humidity_range(text) == expected


def test_a_full_hero_gives_every_fact():
    assert watering_facts({"plantHero": HERO}) == {
        "dryness_threshold_pct": 100,
        "drought_tolerance": "high",
        "dormancy_shift": True,
        "soil_drainage": "fast",
        "temp_min_c": 18.0,
        "temp_max_c": 29.0,
        "rh_min_pct": 20,
        "rh_max_pct": 40,
        "light_need": "bright_indirect",
    }


@pytest.mark.parametrize(
    ("light_min", "need"),
    [
        ("low", "low"),
        ("medium", "medium"),
        ("bright", "bright_indirect"),
        ("full", "direct_sun"),
        ("Bright", "bright_indirect"),
        ("dim", None),
        (None, None),
        (3, None),
    ],
)
def test_the_light_need_is_the_dimmest_documented_light(light_min, need):
    assert watering_facts({"plantHero": {"lightMin": light_min}})["light_need"] == need


@pytest.mark.parametrize(
    ("hero", "column"),
    [
        ({"drynessThreshold": 30}, "dryness_threshold_pct"),
        ({"drynessThreshold": "50"}, "dryness_threshold_pct"),
        ({"drynessThreshold": True}, "dryness_threshold_pct"),
        ({"droughtTolerance": "extreme"}, "drought_tolerance"),
        ({"dormancyShift": "yes"}, "dormancy_shift"),
        ({"soilDrainage": "slow"}, "soil_drainage"),
    ],
)
def test_a_value_outside_the_known_ones_is_none(hero, column):
    assert watering_facts({"plantHero": hero})[column] is None


def test_a_record_without_a_hero_has_no_facts():
    assert set(watering_facts({}).values()) == {None}


def test_the_recommended_parameter_wins_and_the_hero_text_is_the_fallback():
    record = {
        "plantHero": {"temperature": "60-70°F", "humidity": "30-50%"},
        "parameters": {"temperature": {"recommended": "20-25°C"}, "humidity": {"recommended": "?"}},
    }

    facts = watering_facts(record)

    assert (facts["temp_min_c"], facts["temp_max_c"]) == (20.0, 25.0)
    assert (facts["rh_min_pct"], facts["rh_max_pct"]) == (30, 50)


def test_the_profile_carries_the_facts_and_its_hash_follows_each_of_them():
    base = convert_profile(plant_record(plantHero=HERO), ENTRY, date(2026, 11, 22))

    assert (base.dryness_threshold_pct, base.light_need) == (100, "bright_indirect")
    assert (base.temp_min_c, base.temp_max_c) == (18.0, 29.0)
    for key, value in {
        "drynessThreshold": 50,
        "droughtTolerance": "low",
        "dormancyShift": False,
        "soilDrainage": "good",
        "lightMin": "medium",
    }.items():
        changed = convert_profile(
            plant_record(plantHero={**HERO, key: value}), ENTRY, date(2026, 11, 22)
        )
        assert changed.content_sha256 != base.content_sha256, key
    reimported = convert_profile(plant_record(plantHero=HERO), ENTRY, date(2030, 1, 1))
    assert reimported.content_sha256 == base.content_sha256
