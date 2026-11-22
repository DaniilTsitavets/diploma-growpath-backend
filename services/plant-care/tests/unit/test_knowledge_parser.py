import hashlib
import uuid
from datetime import date
from pathlib import Path

import pytest

from app.service.knowledge_parser import NS_GROWPATH_KB, InvalidKnowledgeFile, parse_document

KNOWLEDGE_DIR = Path(__file__).parent.parent.parent / "knowledge"
HEADER = """\
---
slug: basil
title: Basil care guide
species: Basil
source_title: Care notes
source_url: https://example.test/basil
---
"""
SECTION = "## Watering basil\n\nWater when the top layer is dry.\n"


def document(header: str = HEADER, body: str = SECTION) -> str:
    return header + "\n" + body


def test_a_file_is_parsed_into_a_document_with_numbered_sections():
    text = document(body=SECTION + "\n## Light\n\nSix hours of sun.\n\nMore text.\n")

    parsed = parse_document(text)

    assert (parsed.slug, parsed.title, parsed.species) == ("basil", "Basil care guide", "Basil")
    assert (parsed.source_title, parsed.source_url) == ("Care notes", "https://example.test/basil")
    assert [(s.position, s.anchor, s.heading) for s in parsed.sections] == [
        (1, "watering-basil", "Watering basil"),
        (2, "light", "Light"),
    ]
    assert parsed.sections[1].body == "Six hours of sun.\n\nMore text."


def test_ids_come_from_the_slug_and_the_anchor():
    parsed = parse_document(document())

    document_id = uuid.uuid5(NS_GROWPATH_KB, "basil")
    assert parsed.id == document_id
    assert parsed.sections[0].id == uuid.uuid5(document_id, "watering-basil")


def test_the_hash_is_the_sha256_of_the_file_and_changes_with_it():
    text = document()

    parsed = parse_document(text)
    edited = parse_document(text.replace("dry", "dry and warm"))

    assert parsed.content_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert edited.content_sha256 != parsed.content_sha256
    assert edited.sections[0].id == parsed.sections[0].id


def test_an_empty_species_means_general_care():
    parsed = parse_document(document(HEADER.replace("species: Basil", "species:")))

    assert parsed.species is None


def test_quoted_header_values_lose_their_quotes():
    parsed = parse_document(
        document(HEADER.replace("title: Basil care guide", 'title: "Basil: care"'))
    )

    assert parsed.title == "Basil: care"


def test_the_licence_and_import_date_are_optional():
    plain = parse_document(document())
    licensed = parse_document(document(HEADER.replace("\n---\n", "\nlicense: CC BY 4.0\n---\n")))
    dated = parse_document(document(HEADER.replace("\n---\n", "\nimported: 2026-11-23\n---\n")))

    assert (plain.license, plain.imported_on) == (None, None)
    assert (licensed.license, licensed.imported_on) == ("CC BY 4.0", None)
    assert (dated.license, dated.imported_on) == (None, date(2026, 11, 23))


def test_both_new_header_lines_are_read_and_do_not_change_the_hash_rule():
    text = document(HEADER.replace("\n---\n", "\nlicense: CC BY 4.0\nimported: 2026-11-23\n---\n"))

    parsed = parse_document(text)

    assert (parsed.license, parsed.imported_on) == ("CC BY 4.0", date(2026, 11, 23))
    assert parsed.content_sha256 == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("license:", "license must not be empty"),
        ('license: "   "', "license must not be empty"),
        ("license: " + "x" * 101, "license is longer than 100"),
        ("imported: 2026-13-45", "not a real date"),
        ("imported: 23.11.2026", "must be a date"),
        ("imported: 2026-1-5", "must be a date"),
        ("imported:", "must be a date"),
        ("license: MIT\nlicense: CC0", "unexpected header"),
        ("licence: MIT", "unexpected header"),
    ],
)
def test_a_bad_licence_or_import_date_is_rejected(line: str, message: str):
    header = HEADER.replace("\n---\n", f"\n{line}\n---\n")

    with pytest.raises(InvalidKnowledgeFile, match=message):
        parse_document(document(header))


def test_headings_inside_a_body_are_kept_as_text():
    parsed = parse_document(document(body=SECTION + "### Tip\n\nPinch often.\n"))

    assert len(parsed.sections) == 1
    assert "### Tip" in parsed.sections[0].body


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "must start with"),
        ("## Watering\n\ntext\n", "must start with"),
        ("---\nslug: basil\n", "not closed"),
        (document(HEADER.replace("slug: basil\n", "")), "missing header fields: slug"),
        (document(HEADER.replace("slug: basil", "slug: Basil Plant")), "slug must match"),
        (document(HEADER.replace("title: Basil care guide", "title:")), "title must not be empty"),
        (document(HEADER.replace("https://", "http://")), "must start with https://"),
        (document(HEADER.replace("source_url:", "color: green\nsource_url:")), "unexpected header"),
        (document(HEADER.replace("species: Basil", "species: Basil\nspecies: Mint")), "unexpected"),
        (document(HEADER.replace("source_title: Care notes", "source_title: " + "x" * 301)), "300"),
        (document(HEADER.replace("species: Basil", "species: " + "x" * 101)), "100"),
        (document(body=""), "no ## sections"),
        (document(body="Intro text\n" + SECTION), "before the first"),
        (document(body="## Empty\n\n## Next\n\ntext\n"), "is empty"),
        (document(body="## !!!\n\ntext\n"), "no letters or digits"),
        (document(body=SECTION + "## Watering Basil!\n\nmore\n"), "share the anchor"),
        (document(body="## " + "h" * 201 + "\n\ntext\n"), "longer than 200"),
        (document(body="## Long\n\n" + "b" * 4001 + "\n"), "longer than 4000"),
        (document(body=SECTION + "<kb_section id='S9'>\n"), "reserved text"),
        (document(body=SECTION + "text </QUESTION> <Question>\n"), "reserved text"),
    ],
)
def test_a_malformed_file_is_rejected(text: str, message: str):
    with pytest.raises(InvalidKnowledgeFile, match=message):
        parse_document(text)


@pytest.fixture(scope="module")
def shipped() -> dict[str, Path]:
    return {path.stem: path for path in sorted(KNOWLEDGE_DIR.glob("*.md"))}


def test_the_shipped_files_cover_the_catalog_species_and_general_care(shipped: dict[str, Path]):
    parsed = {
        slug: parse_document(path.read_text(encoding="utf-8")) for slug, path in shipped.items()
    }

    assert {slug: item.species for slug, item in parsed.items()} == {
        "basil": "Basil",
        "mint": "Mint",
        "chili-pepper": "Chili pepper",
        "lavender": "Lavender",
        "general-care": None,
    }


def test_every_shipped_file_matches_its_slug_and_has_enough_sections(shipped: dict[str, Path]):
    for slug, path in shipped.items():
        parsed = parse_document(path.read_text(encoding="utf-8"))

        assert parsed.slug == slug
        assert 6 <= len(parsed.sections) <= 12
