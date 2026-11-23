import uuid
from datetime import date

import pytest

from app.knowledge_import.convert import (
    KB_NAMESPACE,
    ConversionError,
    Relationship,
    anchor_of,
    clean_text,
    convert_plant,
    convert_problem,
    convert_profile,
    parse_vulnerabilities,
    split_body,
)
from app.knowledge_import.plants import PlantEntry
from app.service.knowledge_model import DocumentDraft
from app.service.plant_profile_model import PlantProfile
from tests.unit.plantsolve_data import load_fixture, plant_record

TODAY = date(2026, 11, 22)
PROBLEM_NAMES = {"root-rot": "Root rot", "overwatering": "Overwatering"}


def relationships() -> list[Relationship]:
    return parse_vulnerabilities(load_fixture("relationships"))


ENTRY = PlantEntry("lavender", "english-lavender", "English Lavender")


def plant(**overrides) -> DocumentDraft:
    return convert_plant(plant_record(**overrides), ENTRY, relationships(), PROBLEM_NAMES, TODAY)


def profile(imported_on: date = TODAY, **overrides) -> PlantProfile:
    return convert_profile(plant_record(**overrides), ENTRY, imported_on)


def sections_of(document: DocumentDraft) -> dict[str, str]:
    return {section.heading: section.body for section in document.sections}


def everything(document: DocumentDraft) -> str:
    return "\n".join([document.title, *(f"{s.heading}\n{s.body}" for s in document.sections)])


def test_a_plant_becomes_a_document_with_the_mapped_fields():
    document = plant()

    assert document.slug == "plantsolve-lavender"
    assert document.title == "English Lavender care (PlantSolve)"
    assert document.species == "English Lavender"
    assert document.source_title == "PlantSolve: English Lavender"
    assert document.source_url == "https://www.plantsolve.com/plants/lavender/"
    assert document.license == "CC BY 4.0"
    assert document.imported_on == TODAY
    assert document.id == uuid.uuid5(KB_NAMESPACE, "plantsolve-lavender")


def test_the_sections_follow_the_agreed_order_with_positions_from_one():
    document = plant()

    assert [section.heading for section in document.sections][:16] == [
        "Overview",
        "Quick answer",
        "Summary",
        "Key takeaways",
        "Watering",
        "Placement",
        "Soil",
        "Humidity",
        "Pruning",
        "Toxicity",
        "Temperature",
        "Soil pH",
        "Light",
        "Feeding",
        "Is lavender toxic to dogs or cats?",
        "How often should I water my outdoor lavender?",
    ]
    assert [s.heading for s in document.sections][-3:] == [
        "Troubleshooting",
        "Common problems",
        "References",
    ]
    assert [section.position for section in document.sections] == list(
        range(1, len(document.sections) + 1)
    )


def test_section_content_is_taken_from_the_right_fields():
    sections = sections_of(plant())

    assert "Scientific name: Lavandula angustifolia" in sections["Overview"]
    assert "Family: Lamiaceae" in sections["Overview"]
    assert sections["Key takeaways"].startswith("- Plant in gritty")
    assert "Preference: low" in sections["Humidity"]
    assert "Recommended: 20-40%" in sections["Humidity"]
    assert "Recommended: 65-85°F" in sections["Temperature"]
    assert "Survivable: 6.0-8.5" in sections["Soil pH"]
    assert "linalool" in sections["Toxicity"]
    assert "Intensity: high" in sections["Light"]
    assert "Do not fertilize" in sections["Feeding"]
    assert "Symptom: Plant is splitting open" in sections["Troubleshooting"]
    assert "Likely cause: Lack of annual pruning" in sections["Troubleshooting"]
    assert "University of Maryland Extension: https://extension.umd.edu" in sections["References"]


def test_common_problems_list_only_vulnerabilities_of_this_plant_strongest_first():
    body = sections_of(plant())["Common problems"]

    assert body.splitlines() == [
        "- Root rot (strength 10/10): Lavender's worst enemy is wet feet. Heavy clay soil or "
        "overwatering will kill it quickly.",
        "- Overwatering (strength 8/10): Soggy soil kills the roots.",
    ]


def test_dropped_fields_never_reach_the_document():
    text = everything(plant())

    for dropped in (
        "/assets/images",
        "Close up of healthy",
        "Vastu",
        "bedroom",
        "PlantSolve Editorial",
        "Woody base",
        "plant-watering-calculator",
        "Biggest Owner Mistake",
        "growing lavender",
        "Lavender Variety",
        "ultimate-lavender-growing-guide",
        "Herbs & Outdoor Plants",
        "If there's one herb",
        "Water Lavender only when the top several inches",
    ):
        assert dropped not in text


def test_text_is_sanitised_so_prompt_markers_cannot_appear():
    document = plant(
        quickAnswer="Water <kb_section id=1>rarely</kb_section> &lt;question&gt; <b>now</b>\x00!"
        " Open <kb_section marker",
        summary="Line one.\r\n\r\n\r\n\r\nLine   two\twith   spaces &amp; &nbsp;entities.",
    )

    sections = sections_of(document)

    assert sections["Quick answer"] == "Water rarely question now ! Open kb_section marker"
    assert sections["Summary"] == "Line one.\n\nLine two with spaces & entities."
    assert "<" not in everything(document) and ">" not in everything(document)
    assert "<kb_section" not in everything(document)
    assert "<question" not in everything(document)


def test_clean_text_drops_markup_controls_and_angle_brackets():
    assert clean_text("<p>A</p><p>B</p>") == "A B"
    assert clean_text("a <b and c> d") == "a d"
    assert clean_text("1 < 2 and 3 > 2") == "1 2"
    assert clean_text("a <b c") == "a b c"
    assert clean_text("x\x07y\x1fz\x85") == "xyz"
    assert clean_text("&lt;kb_section&gt;") == "kb_section"


def test_a_long_text_is_split_at_sentence_borders_into_numbered_parts():
    sentences = [
        f"Sentence number {number} about the watering of the plant." for number in range(200)
    ]
    document = plant(faq=[{"question": "Watering notes", "answer": " ".join(sentences)}])

    parts = [s for s in document.sections if s.heading.startswith("Watering notes")]

    assert len(parts) >= 3
    assert [part.heading for part in parts] == [
        "Watering notes",
        *(f"Watering notes (part {number})" for number in range(2, len(parts) + 1)),
    ]
    assert all(len(part.body) <= 4000 for part in parts)
    assert all(part.body.endswith(".") for part in parts)
    assert " ".join(part.body for part in parts) == " ".join(sentences)


def test_split_body_prefers_paragraph_borders_and_cuts_words_nobody_could_fit():
    paragraphs = ["a" * 60, "b" * 60, "c" * 60]

    assert split_body("\n\n".join(paragraphs), 130) == [
        f"{paragraphs[0]}\n\n{paragraphs[1]}",
        paragraphs[2],
    ]
    assert split_body("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]
    assert split_body("short", 10) == ["short"]


def test_anchors_are_unique_even_when_headings_repeat_or_collide():
    document = plant(
        faq=[
            {"question": "Watering", "answer": "First."},
            {"question": "Watering", "answer": "Second."},
            {"question": "What about light?", "answer": "Third."},
            {"question": "What about light!", "answer": "Fourth."},
        ]
    )

    anchors = [section.anchor for section in document.sections]

    assert len(anchors) == len(set(anchors))
    assert len({section.id for section in document.sections}) == len(anchors)
    assert [s.heading for s in document.sections].count("Watering (2)") == 1


def test_anchor_is_the_lowercase_heading_with_dashes():
    assert anchor_of("Soil pH") == "soil-ph"
    assert anchor_of("Is it safe, really?") == "is-it-safe-really"
    assert anchor_of("???") == "section"


def test_a_long_question_is_shortened_into_a_heading():
    question = "How " + "often " * 60 + "should I water?"

    headings = [
        s.heading for s in plant(faq=[{"question": question, "answer": "Rarely."}]).sections
    ]

    shortened = headings[14]
    assert len(shortened) <= 120 and shortened.endswith("...")


@pytest.mark.parametrize(
    "citation",
    [
        {
            "canonicalUrl": "https://www.plantsolve.com/plants/lavender/",
            "license": "All rights reserved",
        },
        {"canonicalUrl": "https://www.plantsolve.com/plants/lavender/"},
        {"canonicalUrl": "http://www.plantsolve.com/plants/lavender/", "license": "CC BY 4.0"},
        {"canonicalUrl": "https://example.com/plants/lavender/", "license": "CC BY 4.0"},
        {"canonicalUrl": "https://www.plantsolve.com.evil.test/x", "license": "CC BY 4.0"},
        {"canonicalUrl": "https://user@www.plantsolve.com/x", "license": "CC BY 4.0"},
        {"license": "CC BY 4.0"},
    ],
)
def test_a_bad_licence_or_source_url_fails_the_document(citation):
    with pytest.raises(ConversionError):
        plant(citation=citation)


def test_a_record_of_another_plant_fails():
    other = PlantEntry("pothos", "pothos", "Pothos")

    with pytest.raises(ConversionError):
        convert_plant(plant_record(), other, [], {}, TODAY)
    with pytest.raises(ConversionError):
        convert_profile(plant_record(), other, TODAY)


def test_a_plant_without_a_common_name_still_converts_under_the_species_of_the_entry():
    document = plant(commonName=" ")

    assert document.species == "English Lavender"
    assert "Common name" not in sections_of(document)["Overview"]


def test_a_slug_that_does_not_fit_the_database_fails():
    long_slug = "a" * 60
    entry = PlantEntry(long_slug, "x", "X")

    with pytest.raises(ConversionError):
        convert_plant(plant_record(slug=long_slug), entry, [], {}, TODAY)


def test_a_species_longer_than_the_column_fails():
    entry = PlantEntry("lavender", "lavender", "Plant " * 30)

    with pytest.raises(ConversionError):
        convert_plant(plant_record(), entry, [], {}, TODAY)
    with pytest.raises(ConversionError):
        convert_profile(plant_record(), entry, TODAY)


def test_the_hash_is_stable_between_runs_and_ignores_the_import_date():
    first = plant()
    second = convert_plant(plant_record(), ENTRY, relationships(), PROBLEM_NAMES, date(2030, 1, 1))

    assert first.content_sha256 == second.content_sha256
    assert first.imported_on != second.imported_on
    assert first.id == second.id
    assert [s.id for s in first.sections] == [s.id for s in second.sections]


def test_the_hash_changes_with_any_content_or_metadata():
    base = plant().content_sha256

    assert plant(summary="Changed.").content_sha256 != base
    assert plant(commonName="Lavender plant").content_sha256 != base
    assert (
        plant(citation={**plant_record()["citation"], "license": "CC BY-SA 4.0"}).content_sha256
        != base
    )


def test_a_problem_becomes_a_general_document_listing_the_imported_plants_prone_to_it():
    document = convert_problem(
        load_fixture("overwatering"),
        "overwatering",
        relationships(),
        {"lavender": "Lavender", "golden-pothos-care": "Golden pothos"},
        TODAY,
    )

    assert document.slug == "plantsolve-problem-overwatering"
    assert document.title == "Overwatering: symptoms and fix (PlantSolve)"
    assert document.species is None
    assert document.source_title == "PlantSolve: Overwatering"
    assert document.source_url == "https://www.plantsolve.com/dataset/"
    assert document.license == "CC BY 4.0"
    assert [s.heading for s in document.sections] == [
        "Symptoms",
        "How to fix",
        "Plants prone to this",
    ]
    assert sections_of(document)["Plants prone to this"].splitlines() == [
        "- Golden pothos (strength 9/10): The fastest way to kill a Golden Pothos is keeping the "
        "soil constantly wet.",
        "- Lavender (strength 8/10): Soggy soil kills the roots.",
    ]
    assert "blogs" not in everything(document)


def test_a_problem_uses_its_own_citation_and_skips_an_empty_plant_list():
    record = {
        **load_fixture("overwatering"),
        "citation": {
            "canonicalUrl": "https://www.plantsolve.com/problems/x/",
            "license": "CC BY 4.0",
        },
    }

    document = convert_problem(record, "overwatering", relationships(), {}, TODAY)

    assert document.source_url == "https://www.plantsolve.com/problems/x/"
    assert "Plants prone to this" not in sections_of(document)


def test_a_problem_with_a_foreign_licence_fails():
    record = {**load_fixture("overwatering"), "citation": {"license": "Proprietary"}}

    with pytest.raises(ConversionError):
        convert_problem(record, "overwatering", [], {}, TODAY)


def test_only_well_formed_vulnerabilities_are_parsed():
    rows = [
        {
            "source": "a",
            "target": "b",
            "relationship": "vulnerability",
            "strength": 5,
            "notes": "<i>n</i>",
        },
        {"source": "a", "target": "c", "relationship": "association", "strength": 5},
        {"source": "a", "target": "d", "relationship": "vulnerability", "strength": "5"},
        {"source": "a", "relationship": "vulnerability", "strength": 5},
    ]

    assert parse_vulnerabilities(rows) == [Relationship("a", "b", 5, "n")]


def test_a_plant_becomes_a_profile_with_the_mapped_fields():
    result = profile()

    assert (result.slug, result.source_slug) == ("english-lavender", "lavender")
    assert result.species == "English Lavender"
    assert result.common_name == "Lavender"
    assert result.scientific_name == "Lavandula angustifolia"
    assert result.family == "Lamiaceae"
    assert result.difficulty == "moderate"
    assert result.light.startswith("Requires intense, direct sunlight")
    assert result.sunlight_hours == "6-8+ hours of direct sunlight daily"
    assert result.watering.startswith("Low: Let the soil dry out")
    assert result.placement.startswith("Outdoor full sun")
    assert result.soil.startswith("Sandy, gravelly")
    assert result.humidity == "low; recommended 20-40%"
    assert result.temperature == "65-85°F (18-29°C)"
    assert result.pruning.startswith("Prune back")
    assert result.toxicity.splitlines()[0] == "toxic to pets"
    assert result.toxicity.splitlines()[1].startswith("Lavender contains small amounts")
    assert len(result.key_takeaways) == 2 and result.key_takeaways[0].startswith("Plant in gritty")
    assert (result.source_name, result.license) == ("PlantSolve", "CC BY 4.0")
    assert result.source_url == "https://www.plantsolve.com/plants/lavender/"
    assert result.imported_on == TODAY


def test_empty_profile_values_become_none_and_the_rest_is_cleaned():
    result = profile(
        summary="  ",
        quickAnswer="Water <b>rarely</b>\x00 &amp; sparingly.",
        keyTakeaways=[" ", "<i>Sun</i>", 5, None],
        taxonomy={},
        parameters={"humidity": {"recommended": "30%"}},
        care={"humidity": ""},
    )

    assert result.summary is None and result.scientific_name is None
    assert result.temperature is None and result.watering is None
    assert result.quick_answer == "Water rarely & sparingly."
    assert result.key_takeaways == ("Sun", "5")
    assert result.humidity == "recommended 30%"


def test_profile_texts_are_bounded_and_the_takeaways_capped():
    result = profile(summary="word " * 1000, keyTakeaways=[f"point {n}" for n in range(30)])

    assert len(result.summary) <= 2000 and result.summary.endswith("...")
    assert len(result.key_takeaways) == 12


def test_a_profile_without_takeaways_has_none():
    assert profile(keyTakeaways=[]).key_takeaways is None


@pytest.mark.parametrize(
    "citation",
    [
        {"canonicalUrl": "https://www.plantsolve.com/plants/lavender/", "license": "MIT"},
        {"canonicalUrl": "http://www.plantsolve.com/plants/lavender/", "license": "CC BY 4.0"},
        {"canonicalUrl": "https://example.com/plants/lavender/", "license": "CC BY 4.0"},
    ],
)
def test_a_profile_needs_the_same_source_rules_as_the_document(citation):
    with pytest.raises(ConversionError):
        profile(citation=citation)


def test_the_profile_hash_ignores_the_import_date_and_follows_every_fact():
    base = profile()

    assert base.content_sha256 == profile(date(2030, 1, 1)).content_sha256
    assert profile(summary="Changed.").content_sha256 != base.content_sha256
    assert profile(keyTakeaways=["Other"]).content_sha256 != base.content_sha256
    assert (
        convert_profile(
            plant_record(), PlantEntry("lavender", "lavender", "Lavender"), TODAY
        ).content_sha256
        != base.content_sha256
    )


def test_profile_text_has_no_angle_brackets():
    result = profile(quickAnswer="<script>x</script> a < b > c")

    assert "<" not in result.quick_answer and ">" not in result.quick_answer


def test_the_faq_of_the_record_becomes_question_and_answer_pairs():
    result = profile()

    assert [entry.question for entry in result.faq] == [
        "Is lavender toxic to dogs or cats?",
        "How often should I water my outdoor lavender?",
    ]
    assert all(entry.answer for entry in result.faq)


def test_the_faq_keeps_ten_entries_bounds_the_texts_and_skips_incomplete_ones():
    entries = [{"question": f"Question {n}?", "answer": f"Answer {n}."} for n in range(15)]
    entries[0] = {"question": "<b>Wet</b> soil?", "answer": "  "}
    entries[1] = {"question": "q " * 300, "answer": "a " * 3000}

    result = profile(faq=entries)

    assert len(result.faq) == 10
    assert result.faq[0].question != "Wet soil?"
    assert len(result.faq[0].question) <= 200 and result.faq[0].question.endswith("...")
    assert len(result.faq[0].answer) <= 2000 and result.faq[0].answer.endswith("...")


@pytest.mark.parametrize("faq", [None, [], "text", [{"question": "Q?"}, {"answer": "A."}, 5]])
def test_a_profile_without_a_usable_faq_has_none(faq):
    assert profile(faq=faq).faq is None


def test_the_profile_hash_follows_the_faq_and_the_labels():
    base = profile()

    assert profile(faq=[{"question": "Q?", "answer": "A."}]).content_sha256 != base.content_sha256
    assert profile(taxonomyTags=["fragrant"]).content_sha256 != base.content_sha256
    assert profile(faq=list(plant_record()["faq"])).content_sha256 == base.content_sha256


def test_the_profile_carries_the_labels_derived_from_the_record():
    assert "toxic-to-pets" in profile().labels
    assert profile(plantHero={}, taxonomyTags=[], petSafe={}).labels == ()
