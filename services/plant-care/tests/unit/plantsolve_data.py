import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.knowledge_import.source import NotFound, SourceError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "plantsolve"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def plant_record(**overrides: Any) -> dict[str, Any]:
    return {**load_fixture("lavender"), **overrides}


class FakeSource:
    """Serves the fixtures; `plants` and `problems` map slugs to records."""

    def __init__(self) -> None:
        lavender = load_fixture("lavender")
        self.plants: dict[str, Mapping[str, Any]] = {"lavender": lavender}
        self.problems: dict[str, Mapping[str, Any]] = {
            "overwatering": load_fixture("overwatering"),
            "root-rot": {**load_fixture("overwatering"), "name": "Root rot", "slug": "root-rot"},
        }
        self.index = load_fixture("problems_index")
        self.relationship_rows = load_fixture("relationships")
        self.broken: set[str] = set()

    def plant(self, slug: str) -> Mapping[str, Any]:
        if slug in self.broken:
            raise SourceError(f"{slug}: HTTP 500")
        if slug not in self.plants:
            raise NotFound(slug)
        return copy.deepcopy(self.plants[slug])

    def problems_index(self) -> list[Mapping[str, Any]]:
        return copy.deepcopy(self.index)

    def problem(self, slug: str) -> Mapping[str, Any]:
        if slug not in self.problems:
            raise NotFound(slug)
        return copy.deepcopy(self.problems[slug])

    def relationships(self) -> list[Mapping[str, Any]]:
        return copy.deepcopy(self.relationship_rows)
