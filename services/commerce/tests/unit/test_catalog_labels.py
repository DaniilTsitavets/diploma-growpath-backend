import json
from pathlib import Path

import pytest

from app.catalog_labels import LABEL_FILE, MAX_LABEL_LENGTH, load_label_table
from app.seed import CATALOG, MAX_LABELS_PER_ITEM

PACKS = [item for item in CATALOG if item.item_type == "pack"]


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "labels.json"
    path.write_text(content, encoding="utf-8")
    return path


def test_the_committed_file_has_an_entry_for_every_pack_and_for_nothing_else():
    table = load_label_table()

    assert set(table) == {pack.plant_slug for pack in PACKS}


def test_every_pack_gets_labels_within_the_limit():
    table = load_label_table()

    for pack in PACKS:
        wanted = pack.wanted_labels(table)
        assert table[pack.plant_slug], pack.slug
        assert len(wanted) <= MAX_LABELS_PER_ITEM
        assert all(len(label) <= MAX_LABEL_LENGTH for label in wanted)


def test_the_committed_file_is_stored_sorted_and_stable():
    text = LABEL_FILE.read_text(encoding="utf-8")
    table = json.loads(text)

    assert list(table) == sorted(table)
    assert all(labels == sorted(labels) for labels in table.values())
    assert text == json.dumps(table, indent=2) + "\n"


def test_a_hand_set_label_is_added_to_the_derived_ones():
    monstera = next(item for item in PACKS if item.slug == "monstera-deliciosa")

    assert monstera.wanted_labels({"monstera-deliciosa": ("beginner",)}) == {"rare", "beginner"}


def test_an_item_without_a_plant_has_only_its_hand_set_labels():
    sensor = next(item for item in CATALOG if item.item_type == "sensor")

    assert sensor.wanted_labels(load_label_table()) == frozenset()


def test_too_many_labels_for_one_item_are_refused():
    pack = PACKS[0]
    table = {pack.plant_slug: tuple(f"label-{n}" for n in range(MAX_LABELS_PER_ITEM + 1))}

    with pytest.raises(ValueError, match="more than"):
        pack.wanted_labels(table)


def test_a_missing_file_fails_loudly(tmp_path: Path):
    with pytest.raises(ValueError, match="cannot read"):
        load_label_table(tmp_path / "absent.json")


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"cactus": "beginner"}',
        '{"cactus": ["Beginner"]}',
        '{"cactus": ["low maintenance"]}',
        '{"cactus": [""]}',
        '{"cactus": [1]}',
        '{"cactus": ["beginner", "beginner"]}',
        '{"cactus": ["' + "a" * (MAX_LABEL_LENGTH + 1) + '"]}',
    ],
)
def test_a_malformed_file_is_refused(tmp_path: Path, content: str):
    with pytest.raises(ValueError):
        load_label_table(write(tmp_path, content))


def test_a_valid_file_is_read_as_tuples(tmp_path: Path):
    path = write(tmp_path, '{"cactus": ["beginner", "pet-safe"], "lavender": []}')

    assert load_label_table(path) == {"cactus": ("beginner", "pet-safe"), "lavender": ()}
