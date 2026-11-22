import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.knowledge_import.convert import (
    PLANT_PREFIX,
    PROBLEM_PREFIX,
    ConversionError,
    Relationship,
    convert_plant,
    convert_problem,
    parse_vulnerabilities,
)
from app.knowledge_import.source import SLUG, KnowledgeSource, SourceError
from app.service.knowledge_model import DocumentDraft, SyncResult
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    dry_run: bool = False
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.failed

    def record(self, slug: str, result: SyncResult) -> None:
        {
            SyncResult.CREATED: self.created,
            SyncResult.UPDATED: self.updated,
            SyncResult.UNCHANGED: self.unchanged,
        }[result].append(slug)

    def summary(self) -> str:
        line = (
            f"created={len(self.created)} updated={len(self.updated)} "
            f"unchanged={len(self.unchanged)} failed={len(self.failed)} stale={len(self.stale)}"
        )
        return f"{line} (dry run, nothing written)" if self.dry_run else line


def read_plant_slugs(path: Path) -> list[str]:
    """One slug per line; blank lines and `#` comments are ignored, repeats are dropped."""
    slugs: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        slug = line.split("#", 1)[0].strip()
        if not slug:
            continue
        if not SLUG.fullmatch(slug):
            raise ValueError(f"{path}:{number}: invalid plant slug {slug!r}")
        if slug not in slugs:
            slugs.append(slug)
    return slugs


class ImportRunner:
    """Fetches the listed plants and the known problems and stores each as a knowledge document.

    A document that cannot be fetched, converted or stored is reported and skipped; the rest go on.
    """

    def __init__(
        self,
        source: KnowledgeSource,
        unit_of_work: Callable[[], UnitOfWork],
        plant_slugs: Sequence[str],
        today: Callable[[], date],
        dry_run: bool = False,
    ) -> None:
        self._source = source
        self._unit_of_work = unit_of_work
        self._plant_slugs = plant_slugs
        self._today = today
        self._dry_run = dry_run

    def run(self) -> ImportReport:
        """Raises SourceError when the shared relationships or problem list are unavailable."""
        relationships = parse_vulnerabilities(self._source.relationships())
        problems = self._problem_names(self._source.problems_index())
        report = ImportReport(dry_run=self._dry_run)
        plants = self._plant_documents(relationships, problems, report)
        plant_names = {slug: draft.species for slug, draft in plants.items() if draft.species}
        documents = [
            *plants.values(),
            *self._problem_documents(relationships, plant_names, problems, report),
        ]
        if self._dry_run:
            self._compare(documents, report)
        else:
            for document in documents:
                self._store(document, report)
        planned = {f"{PLANT_PREFIX}{slug}" for slug in self._plant_slugs}
        planned |= {f"{PROBLEM_PREFIX}{slug}" for slug in problems}
        self._report_stale(planned, report)
        return report

    def _problem_names(self, index: Sequence[Mapping[str, Any]]) -> dict[str, str]:
        names: dict[str, str] = {}
        for entry in index:
            slug, name = entry.get("slug"), entry.get("name")
            if isinstance(slug, str) and SLUG.fullmatch(slug):
                names[slug] = name if isinstance(name, str) else slug
            else:
                logger.warning(
                    "problem without a valid slug skipped", extra={"entry": str(entry)[:200]}
                )
        return names

    def _plant_documents(
        self,
        relationships: Sequence[Relationship],
        problems: Mapping[str, str],
        report: ImportReport,
    ) -> dict[str, DocumentDraft]:
        documents: dict[str, DocumentDraft] = {}
        for slug in self._plant_slugs:
            document = self._attempt(
                f"{PLANT_PREFIX}{slug}",
                report,
                lambda slug=slug: convert_plant(
                    self._source.plant(slug), slug, relationships, problems, self._today()
                ),
            )
            if document is not None:
                documents[slug] = document
        return documents

    def _problem_documents(
        self,
        relationships: Sequence[Relationship],
        plant_names: Mapping[str, str],
        problems: Mapping[str, str],
        report: ImportReport,
    ) -> list[DocumentDraft]:
        documents = []
        for slug in problems:
            document = self._attempt(
                f"{PROBLEM_PREFIX}{slug}",
                report,
                lambda slug=slug: convert_problem(
                    self._source.problem(slug), slug, relationships, plant_names, self._today()
                ),
            )
            if document is not None:
                documents.append(document)
        return documents

    def _attempt(
        self, slug: str, report: ImportReport, build: Callable[[], DocumentDraft]
    ) -> DocumentDraft | None:
        try:
            return build()
        except (SourceError, ConversionError) as error:
            self._fail(slug, str(error), report)
            return None

    def _store(self, document: DocumentDraft, report: ImportReport) -> None:
        try:
            with self._unit_of_work() as uow:
                result = uow.knowledge.sync(document)
                uow.commit()
        except SQLAlchemyError as error:
            self._fail(document.slug, f"database error: {error.__class__.__name__}", report)
            return
        self._record(document.slug, result, report, "knowledge document synced")

    def _compare(self, documents: Sequence[DocumentDraft], report: ImportReport) -> None:
        with self._unit_of_work() as uow:
            stored = uow.knowledge.content_hashes([document.slug for document in documents])
        for document in documents:
            if document.slug not in stored:
                result = SyncResult.CREATED
            elif stored[document.slug] != document.content_sha256:
                result = SyncResult.UPDATED
            else:
                result = SyncResult.UNCHANGED
            self._record(document.slug, result, report, "knowledge document compared")

    def _report_stale(self, planned: set[str], report: ImportReport) -> None:
        with self._unit_of_work() as uow:
            stored = uow.knowledge.slugs_with_prefix(PLANT_PREFIX)
        report.stale = sorted(set(stored) - planned)
        for slug in report.stale:
            logger.warning("stored PlantSolve document is no longer imported", extra={"slug": slug})

    def _record(self, slug: str, result: SyncResult, report: ImportReport, message: str) -> None:
        report.record(slug, result)
        logger.info(message, extra={"slug": slug, "sync_result": result.value})

    def _fail(self, slug: str, reason: str, report: ImportReport) -> None:
        report.failed[slug] = reason
        logger.error("knowledge document failed", extra={"slug": slug, "reason": reason})
