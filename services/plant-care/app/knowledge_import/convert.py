import hashlib
import html
import json
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from app.service.knowledge_model import DocumentDraft, SectionDraft

KB_NAMESPACE = uuid.UUID("5e348c49-ad5f-422f-bdb5-afa4290841a4")
ALLOWED_HOST = "www.plantsolve.com"
DEFAULT_LICENSE = "CC BY 4.0"
# Problem records have no page of their own on the site; this page describes the open dataset.
DATASET_URL = f"https://{ALLOWED_HOST}/dataset/"
PLANT_PREFIX = "plantsolve-"
PROBLEM_PREFIX = "plantsolve-problem-"

MAX_SLUG = 64
MAX_TITLE = 200
MAX_SPECIES = 100
MAX_SOURCE_TITLE = 300
MAX_SOURCE_URL = 1000
MAX_LICENSE = 100
MAX_HEADING = 200
MAX_BODY = 4000
MAX_FAQ_HEADING = 120

_TAG = re.compile(r"<[^>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_INLINE_SPACE = re.compile(r"[^\S\n]+")
_AROUND_NEWLINE = re.compile(r" ?\n ?")
_BLANK_LINES = re.compile(r"\n{3,}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DOCUMENT_SLUG = re.compile(r"^[a-z0-9-]{1,64}$")


class ConversionError(Exception):
    """The record cannot become a valid knowledge document."""


@dataclass(frozen=True)
class Relationship:
    source: str
    target: str
    strength: int
    notes: str


def parse_vulnerabilities(rows: Iterable[Mapping[str, Any]]) -> list[Relationship]:
    """Plant-to-problem vulnerability rows; malformed rows and other kinds are skipped."""
    parsed = []
    for row in rows:
        strength = row.get("strength")
        source, target = row.get("source"), row.get("target")
        if (
            row.get("relationship") == "vulnerability"
            and isinstance(source, str)
            and isinstance(target, str)
            and isinstance(strength, int)
            and not isinstance(strength, bool)
        ):
            parsed.append(
                Relationship(source, target, strength, clean_line(_text(row.get("notes"))))
            )
    return parsed


def clean_text(value: str) -> str:
    """Plain text safe to put into a prompt: no markup, no `<` or `>`, no control characters."""
    text = html.unescape(_TAG.sub(" ", value))
    text = _CONTROL.sub("", text.replace("<", "").replace(">", ""))
    text = _INLINE_SPACE.sub(" ", text)
    text = _AROUND_NEWLINE.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def clean_line(value: str) -> str:
    return " ".join(clean_text(value).split())


def split_body(text: str, limit: int = MAX_BODY) -> list[str]:
    """Pieces of at most `limit` characters, cut at paragraph, then sentence, then word borders."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for separator, unit in _units(text, limit):
        if current and len(current) + len(separator) + len(unit) <= limit:
            current += separator + unit
        else:
            if current:
                chunks.append(current)
            current = unit
    chunks.append(current)
    return chunks


def convert_plant(
    record: Mapping[str, Any],
    slug: str,
    relationships: Sequence[Relationship],
    problem_names: Mapping[str, str],
    imported_on: date,
) -> DocumentDraft:
    if record.get("slug") != slug:
        raise ConversionError(f"the record is for {record.get('slug')!r}, not {slug!r}")
    common_name = _text(record.get("commonName"))
    if not common_name:
        raise ConversionError("the plant has no common name")
    citation = _mapping(record.get("citation"))
    sections = _Sections()
    for heading, body in _plant_sections(record, common_name, slug, relationships, problem_names):
        sections.add(heading, body)
    return _document(
        slug=f"{PLANT_PREFIX}{slug}",
        title=f"{common_name} care (PlantSolve)",
        species=common_name,
        source_title=f"PlantSolve: {common_name}",
        source_url=_valid_url(citation.get("canonicalUrl")),
        license=_valid_license(citation.get("license")),
        sections=sections,
        imported_on=imported_on,
    )


def convert_problem(
    record: Mapping[str, Any],
    slug: str,
    relationships: Sequence[Relationship],
    plant_names: Mapping[str, str],
    imported_on: date,
) -> DocumentDraft:
    if record.get("slug") != slug:
        raise ConversionError(f"the record is for {record.get('slug')!r}, not {slug!r}")
    name = _text(record.get("name"))
    if not name:
        raise ConversionError("the problem has no name")
    citation = _mapping(record.get("citation"))
    url = record.get("canonicalUrl") or citation.get("canonicalUrl")
    sections = _Sections()
    sections.add("Symptoms", _text(record.get("symptom")))
    sections.add("How to fix", _text(record.get("fix")))
    sections.add("Plants prone to this", _prone_plants(slug, relationships, plant_names))
    return _document(
        slug=f"{PROBLEM_PREFIX}{slug}",
        title=f"{name}: symptoms and fix (PlantSolve)",
        species=None,
        source_title=f"PlantSolve: {name}",
        source_url=_valid_url(url or DATASET_URL),
        license=_valid_license(citation.get("license") or DEFAULT_LICENSE),
        sections=sections,
        imported_on=imported_on,
    )


class _Sections:
    """Collects sections, cleaning them, splitting long bodies and keeping anchors unique."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []
        self._anchors: set[str] = set()

    def add(self, heading: str, body: str) -> None:
        heading, body = clean_line(heading), clean_text(body)
        if not heading or not body:
            return
        for number, chunk in enumerate(split_body(body), start=1):
            self._add_unique(heading, "" if number == 1 else f" (part {number})", chunk)

    def _add_unique(self, heading: str, suffix: str, body: str) -> None:
        candidate = _fit_heading(heading, suffix)
        duplicate = 1
        while anchor_of(candidate) in self._anchors:
            duplicate += 1
            candidate = _fit_heading(heading, f"{suffix} ({duplicate})")
        self._anchors.add(anchor_of(candidate))
        self.items.append((candidate, body))


def anchor_of(heading: str) -> str:
    return _NON_ALNUM.sub("-", heading.lower()).strip("-") or "section"


def _fit_heading(heading: str, suffix: str) -> str:
    return heading[: MAX_HEADING - len(suffix)].rstrip() + suffix


def _units(text: str, limit: int) -> Iterator[tuple[str, str]]:
    for paragraph_number, paragraph in enumerate(text.split("\n\n")):
        paragraph_separator = "\n\n" if paragraph_number else ""
        if len(paragraph) <= limit:
            yield paragraph_separator, paragraph
            continue
        first = True
        for sentence in _SENTENCE_END.split(paragraph):
            for piece in _word_slices(sentence, limit):
                yield (paragraph_separator if first else " "), piece
                first = False


def _word_slices(text: str, limit: int) -> Iterator[str]:
    current = ""
    for word in text.split(" "):
        while len(word) > limit:
            if current:
                yield current
                current = ""
            yield word[:limit]
            word = word[limit:]
        if current and len(current) + 1 + len(word) > limit:
            yield current
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        yield current


def _document(
    slug: str,
    title: str,
    species: str | None,
    source_title: str,
    source_url: str,
    license: str,
    sections: _Sections,
    imported_on: date,
) -> DocumentDraft:
    _require(bool(_DOCUMENT_SLUG.fullmatch(slug)), f"invalid document slug {slug!r}")
    _require(len(title) <= MAX_TITLE, "the title is too long")
    _require(species is None or len(species) <= MAX_SPECIES, "the species is too long")
    _require(len(source_title) <= MAX_SOURCE_TITLE, "the source title is too long")
    _require(bool(sections.items), "the record has no usable text")
    document_id = uuid.uuid5(KB_NAMESPACE, slug)
    return DocumentDraft(
        id=document_id,
        slug=slug,
        title=title,
        species=species,
        source_title=source_title,
        source_url=source_url,
        content_sha256=content_hash(
            title, species, source_title, source_url, license, sections.items
        ),
        sections=[
            SectionDraft(
                id=uuid.uuid5(document_id, anchor_of(heading)),
                position=position,
                anchor=anchor_of(heading),
                heading=heading,
                body=body,
            )
            for position, (heading, body) in enumerate(sections.items, start=1)
        ],
        license=license,
        imported_on=imported_on,
    )


def content_hash(
    title: str,
    species: str | None,
    source_title: str,
    source_url: str,
    license: str,
    sections: Sequence[tuple[str, str]],
) -> str:
    """Hash of everything a re-import could change, except the import date."""
    canonical = json.dumps(
        {
            "title": title,
            "species": species,
            "source_title": source_title,
            "source_url": source_url,
            "license": license,
            "sections": [[heading, body] for heading, body in sections],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConversionError(message)


def _valid_url(value: object) -> str:
    url = _text(value)
    parts = urlsplit(url)
    _require(
        parts.scheme == "https"
        and parts.hostname == ALLOWED_HOST
        and parts.username is None
        and len(url) <= MAX_SOURCE_URL
        and url == value,
        f"the source URL is not an https page of {ALLOWED_HOST}",
    )
    return url


def _valid_license(value: object) -> str:
    license = _text(value)
    _require(
        license.startswith("CC BY") and len(license) <= MAX_LICENSE,
        f"the licence {license!r} is not a CC BY licence",
    )
    return license


def _text(value: object) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _lines(pairs: Iterable[tuple[str, object]]) -> str:
    return "\n".join(f"{label}: {text}" for label, value in pairs if (text := _text(value)))


def _ranges(parameter: object) -> str:
    values = _mapping(parameter)
    return _lines(
        [("Recommended", values.get("recommended")), ("Survivable", values.get("survivable"))]
    )


def _bullets(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item)


def _problem_line(name: str, relationship: Relationship) -> str:
    notes = f": {relationship.notes}" if relationship.notes else ""
    return f"- {name} (strength {relationship.strength}/10){notes}"


def _readable(slug: str) -> str:
    return slug.replace("-", " ").capitalize()


def _plant_sections(
    record: Mapping[str, Any],
    common_name: str,
    slug: str,
    relationships: Sequence[Relationship],
    problem_names: Mapping[str, str],
) -> Iterator[tuple[str, str]]:
    care = _mapping(record.get("care"))
    parameters = _mapping(record.get("parameters"))
    lighting = _mapping(record.get("lighting"))
    nutrients = _mapping(record.get("nutrients"))
    taxonomy = _mapping(record.get("taxonomy"))
    humidity = _lines([("Preference", care.get("humidity"))])
    yield (
        "Overview",
        _lines(
            [
                ("Common name", common_name),
                ("Scientific name", taxonomy.get("sciName")),
                ("Family", taxonomy.get("family")),
                ("Difficulty", care.get("difficulty")),
            ]
        ),
    )
    yield "Quick answer", _text(record.get("quickAnswer"))
    yield "Summary", _text(record.get("summary"))
    yield "Key takeaways", _bullets(_text(item) for item in _sequence(record.get("keyTakeaways")))
    yield "Watering", _text(care.get("watering"))
    yield "Placement", _text(care.get("placement"))
    yield "Soil", _text(care.get("soil"))
    yield (
        "Humidity",
        "\n".join(part for part in (humidity, _ranges(parameters.get("humidity"))) if part),
    )
    yield "Pruning", _text(care.get("pruning"))
    yield (
        "Toxicity",
        _lines([("Toxicity", care.get("toxicity")), ("Note", care.get("toxicityNote"))]),
    )
    yield "Temperature", _ranges(parameters.get("temperature"))
    yield "Soil pH", _ranges(parameters.get("soilPH"))
    yield (
        "Light",
        _lines(
            [
                ("Description", lighting.get("description")),
                ("Sunlight hours", lighting.get("sunlightHours")),
                ("Intensity", lighting.get("intensity")),
                ("Best indoor window", lighting.get("indoorWindow")),
                ("Shade tolerance", lighting.get("shadeTolerance")),
            ]
        ),
    )
    yield (
        "Feeding",
        _lines(
            [
                ("Fertilizer frequency", nutrients.get("fertilizerFrequency")),
                ("Nitrogen demand", nutrients.get("nitrogenDemand")),
                ("Phosphate demand", nutrients.get("phosphateDemand")),
                ("Potassium demand", nutrients.get("potassiumDemand")),
                ("Micronutrients", nutrients.get("micronutrientNotes")),
                ("Organic options", nutrients.get("organicOptions")),
            ]
        ),
    )
    yield from _faq_sections(_sequence(record.get("faq")))
    yield "Troubleshooting", _troubleshooting(_sequence(record.get("troubleshooting")))
    yield "Common problems", _common_problems(slug, relationships, problem_names)
    yield "References", _references(_sequence(record.get("references")))


def _faq_sections(entries: list[Any]) -> Iterator[tuple[str, str]]:
    for entry in map(_mapping, entries):
        question = clean_line(_text(entry.get("question")))
        yield _shorten(question, MAX_FAQ_HEADING), _text(entry.get("answer"))


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def _troubleshooting(entries: list[Any]) -> str:
    blocks = (
        _lines(
            [
                ("Symptom", entry.get("symptom")),
                ("Likely cause", entry.get("diagnosis")),
                ("What to do", entry.get("treatment")),
                ("Prevention", entry.get("prevention")),
            ]
        )
        for entry in map(_mapping, entries)
    )
    return "\n\n".join(block for block in blocks if block)


def _common_problems(
    slug: str, relationships: Sequence[Relationship], problem_names: Mapping[str, str]
) -> str:
    own = sorted(
        (item for item in relationships if item.source == slug),
        key=lambda item: (-item.strength, item.target),
    )
    return "\n".join(
        _problem_line(problem_names.get(item.target) or _readable(item.target), item)
        for item in own
    )


def _prone_plants(
    slug: str, relationships: Sequence[Relationship], plant_names: Mapping[str, str]
) -> str:
    prone = sorted(
        (item for item in relationships if item.target == slug and item.source in plant_names),
        key=lambda item: (plant_names[item.source], item.source),
    )
    return "\n".join(_problem_line(plant_names[item.source], item) for item in prone)


def _references(entries: list[Any]) -> str:
    lines = []
    for entry in map(_mapping, entries):
        title, url = _text(entry.get("title")), _text(entry.get("url"))
        if title or url:
            lines.append(f"{title}: {url}" if title and url else title or url)
    return _bullets(lines)
