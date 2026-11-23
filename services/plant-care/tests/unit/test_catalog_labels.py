import json
import re
from datetime import date
from pathlib import Path

import pytest

from app.knowledge_import.__main__ import main
from app.knowledge_import.catalog_labels import derive_labels, write_label_table
from app.knowledge_import.label_export import export_catalog_labels
from app.knowledge_import.plants import PlantEntry
from app.knowledge_import.source import SourceError
from app.knowledge_import.watering_facts import watering_facts
from tests.unit.plantsolve_data import FakeSource, plant_record

TODAY = date(2026, 11, 22)
LABEL = r"^[a-z0-9]+(-[a-z0-9]+)*$"


def labels(hero: dict | None = None, **record) -> tuple[str, ...]:
    full = {"plantHero": hero or {}, **record}
    return derive_labels(full, watering_facts(full))


@pytest.mark.parametrize(
    ("difficulty", "expected"),
    [
        ("beginner", "beginner"),
        (" Intermediate ", "intermediate"),
        ("advanced", "advanced"),
        ("expert", None),
        (None, None),
    ],
)
def test_difficulty_follows_the_hero_value(difficulty, expected):
    found = {"beginner", "intermediate", "advanced"} & set(labels({"difficulty": difficulty}))

    assert found == ({expected} if expected else set())


@pytest.mark.parametrize(
    ("hero", "expected"),
    [
        ({"difficulty": "beginner", "droughtTolerance": "high"}, True),
        ({"difficulty": "beginner", "droughtTolerance": "medium"}, True),
        ({"difficulty": "beginner", "droughtTolerance": "low"}, False),
        ({"difficulty": "beginner"}, False),
        ({"difficulty": "intermediate", "droughtTolerance": "high"}, False),
    ],
)
def test_low_maintenance_needs_a_beginner_plant_that_forgives_missed_watering(hero, expected):
    assert ("low-maintenance" in labels(hero)) is expected


@pytest.mark.parametrize(
    ("hero", "expected"),
    [
        ({"lightMin": "low"}, ["low-light"]),
        ({"lightMin": "medium"}, []),
        ({"lightMin": "bright", "light": "Bright indirect"}, ["bright-light"]),
        ({"lightMin": "bright", "light": "Full sun"}, ["bright-light", "direct-sun"]),
        ({"lightMin": "full"}, ["bright-light", "direct-sun"]),
        ({"light": "Full sun to bright indirect"}, ["direct-sun"]),
        ({}, []),
    ],
)
def test_light_labels(hero, expected):
    light = {"low-light", "bright-light", "direct-sun"}

    assert sorted(light & set(labels(hero))) == expected


@pytest.mark.parametrize(
    ("hero", "expected"),
    [
        ({"droughtTolerance": "high"}, ["drought-tolerant"]),
        ({"droughtTolerance": "low", "drynessThreshold": 25}, ["likes-moisture"]),
        ({"droughtTolerance": "low", "drynessThreshold": 50}, []),
        ({"droughtTolerance": "medium", "drynessThreshold": 25}, []),
        ({}, []),
    ],
)
def test_watering_labels(hero, expected):
    watering = {"drought-tolerant", "likes-moisture"}

    assert sorted(watering & set(labels(hero))) == expected


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"plantHero": {"petSafety": "safe"}}, "pet-safe"),
        ({"plantHero": {"petSafety": "Toxic"}}, "toxic-to-pets"),
        ({"plantHero": {"petSafety": "safe"}, "petSafe": {"cat": False, "dog": False}}, "pet-safe"),
        (
            {"plantHero": {"petSafety": "unknown"}, "petSafe": {"cat": True, "dog": True}},
            "pet-safe",
        ),
        ({"plantHero": {}, "petSafe": {"cat": True, "dog": False}}, "toxic-to-pets"),
        ({"plantHero": {"petSafety": "unknown"}, "petSafe": {"cat": None, "dog": None}}, None),
        ({"plantHero": {}}, None),
    ],
)
def test_pet_labels(record, expected):
    found = {"pet-safe", "toxic-to-pets"} & set(derive_labels(record, watering_facts(record)))

    assert found == ({expected} if expected else set())


@pytest.mark.parametrize(
    ("humidity", "expected"),
    [
        ("60-80%", True),
        ("60% to 80%", True),
        ("50-80%", True),
        ("60%-70%", True),
        ("50-70%", False),
        ("40-60%", False),
        ("low-medium", False),
        (None, False),
    ],
)
def test_humidity_lover_needs_a_high_floor_or_ceiling(humidity, expected):
    assert ("humidity-lover" in labels({"humidity": humidity})) is expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ("6-12 inches indoors", "compact"),
        ("25-30 cm (10-12 in)", "compact"),
        ("1-2 feet", "compact"),
        ("60-90 cm (2-3 ft)", None),
        ("2-3 feet indoors", None),
        ("3-6 feet indoors", "large"),
        ("Trailing up to 10 feet indoors", "large"),
        ("10-15 feet indoors with support", "large"),
        ("Varies widely", None),
        (None, None),
    ],
)
def test_size_labels_follow_the_top_of_the_first_measurement(size, expected):
    found = {"compact", "large"} & set(labels({"matureSize": size}))

    assert found == ({expected} if expected else set())


def test_flowering_is_only_a_true_flag():
    assert "flowering" in labels({"flowering": True})
    assert "flowering" not in labels({"flowering": False})
    assert "flowering" not in labels({"flowering": "yes"})


def test_only_the_useful_taxonomy_tags_become_labels():
    tags = ["Indoor Plants", "air-purifying", "Hanging Plants", "trailing-vine", "herbs", "vastu"]

    assert labels(taxonomyTags=tags) == ("air-purifying", "hanging", "herb")


def test_malformed_input_gives_no_labels():
    assert labels(taxonomyTags="herbs") == ()
    assert derive_labels({"plantHero": "x"}, watering_facts({})) == ()


def test_the_labels_are_sorted_unique_kebab_words():
    result = labels(
        {
            "difficulty": "beginner",
            "droughtTolerance": "high",
            "lightMin": "low",
            "flowering": True,
            "petSafety": "safe",
        },
        taxonomyTags=["hanging-plants", "trailing-vine"],
    )

    assert list(result) == sorted(set(result))
    assert all(re.fullmatch(LABEL, label) for label in result)


def test_the_fixture_plant_gets_its_labels():
    record = plant_record()

    assert derive_labels(record, watering_facts(record)) == (
        "direct-sun",
        "herb",
        "intermediate",
        "toxic-to-pets",
    )


def test_the_table_is_written_sorted_with_a_final_newline(tmp_path: Path):
    path = tmp_path / "labels.json"

    write_label_table(path, {"b": ["x", "y"], "a": []})

    assert (
        path.read_text(encoding="utf-8") == '{\n  "a": [],\n  "b": [\n    "x",\n    "y"\n  ]\n}\n'
    )


def test_the_export_writes_the_labels_of_every_listed_plant(tmp_path: Path):
    path = tmp_path / "labels.json"
    entries = [PlantEntry("lavender", "english-lavender", "English Lavender")]

    table = export_catalog_labels(FakeSource(), entries, lambda: TODAY, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "english-lavender": list(table["english-lavender"])
    }
    assert table["english-lavender"]


def test_an_unfetchable_plant_writes_nothing(tmp_path: Path):
    path = tmp_path / "labels.json"
    entries = [
        PlantEntry("lavender", "lavender", "Lavender"),
        PlantEntry("ghost", "ghost", "Ghost"),
    ]

    with pytest.raises(SourceError, match="ghost"):
        export_catalog_labels(FakeSource(), entries, lambda: TODAY, path)

    assert not path.exists()


def test_the_command_writes_the_label_file_without_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        "app.knowledge_import.__main__.PlantSolveSource", lambda client, policy: FakeSource()
    )
    monkeypatch.delenv("PLANT_CARE_DATABASE_URL", raising=False)
    plants = tmp_path / "plants.txt"
    plants.write_text("lavender | lavender | Lavender\n", encoding="utf-8")
    target = tmp_path / "labels.json"

    code = main(["--write-catalog-labels", str(target), "--plants-file", str(plants)])

    assert code == 0
    assert list(json.loads(target.read_text(encoding="utf-8"))) == ["lavender"]
    assert "wrote the labels of 1 plants" in capsys.readouterr().out


def test_the_command_fails_and_writes_nothing_when_a_plant_cannot_be_converted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = FakeSource()
    source.plants["lavender"] = {**source.plants["lavender"], "citation": {"license": "MIT"}}
    monkeypatch.setattr(
        "app.knowledge_import.__main__.PlantSolveSource", lambda client, policy: source
    )
    plants = tmp_path / "plants.txt"
    plants.write_text("lavender | lavender | Lavender\n", encoding="utf-8")
    target = tmp_path / "labels.json"

    assert main(["--write-catalog-labels", str(target), "--plants-file", str(plants)]) == 1
    assert not target.exists()
