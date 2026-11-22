from dataclasses import dataclass
from pathlib import Path

from app.knowledge_import.source import SLUG
from app.service.plants import PLANT_SLUG

MAX_SPECIES = 80


@dataclass(frozen=True)
class PlantEntry:
    """A plant to import: its PlantSolve slug, our own slug and the species the journals use."""

    source_slug: str
    slug: str
    species: str


def read_plant_entries(path: Path) -> list[PlantEntry]:
    """One `plantsolve-slug | our-slug | Species` per line; blanks and `#` comments are skipped."""
    entries: list[PlantEntry] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.split("#", 1)[0].strip()
        if text:
            entry = _parse_line(text, f"{path}:{number}")
            _reject_repeat(entry, entries, f"{path}:{number}")
            entries.append(entry)
    return entries


def _parse_line(text: str, where: str) -> PlantEntry:
    fields = [field.strip() for field in text.split("|")]
    if len(fields) != 3:
        raise ValueError(f"{where}: expected `plantsolve-slug | our-slug | Species`")
    source_slug, slug, species = fields
    if not SLUG.fullmatch(source_slug):
        raise ValueError(f"{where}: invalid PlantSolve slug {source_slug!r}")
    if not PLANT_SLUG.fullmatch(slug):
        raise ValueError(f"{where}: invalid plant slug {slug!r}")
    if not 1 <= len(species) <= MAX_SPECIES:
        raise ValueError(f"{where}: the species must have 1 to {MAX_SPECIES} characters")
    return PlantEntry(source_slug, slug, species)


def _reject_repeat(entry: PlantEntry, entries: list[PlantEntry], where: str) -> None:
    for known in entries:
        if entry.source_slug == known.source_slug:
            raise ValueError(f"{where}: PlantSolve slug {entry.source_slug!r} is listed twice")
        if entry.slug == known.slug:
            raise ValueError(f"{where}: plant slug {entry.slug!r} is listed twice")
