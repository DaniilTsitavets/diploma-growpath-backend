from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from app.knowledge_import.catalog_labels import write_label_table
from app.knowledge_import.convert import convert_profile
from app.knowledge_import.plants import PlantEntry
from app.knowledge_import.source import KnowledgeSource


def export_catalog_labels(
    source: KnowledgeSource,
    plants: Sequence[PlantEntry],
    today: Callable[[], date],
    path: Path,
) -> dict[str, tuple[str, ...]]:
    """Writes `path` only when every plant could be fetched and converted.

    Raises SourceError or ConversionError otherwise: a partial table would make commerce drop the
    labels of the missing plants.
    """
    table = {
        entry.slug: convert_profile(source.plant(entry.source_slug), entry, today()).labels
        for entry in plants
    }
    write_label_table(path, table)
    return table
