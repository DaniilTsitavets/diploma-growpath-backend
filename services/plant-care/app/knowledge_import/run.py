import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.knowledge_import.convert import (
    PLANT_PREFIX,
    PROBLEM_PREFIX,
    ConversionError,
    Relationship,
    convert_plant,
    convert_problem,
    convert_profile,
    parse_vulnerabilities,
)
from app.knowledge_import.plants import PlantEntry
from app.knowledge_import.source import SLUG, KnowledgeSource, SourceError
from app.service.knowledge_model import DocumentDraft, SyncResult
from app.service.plant_profile_model import PlantProfile
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass
class Outcomes:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def record(self, slug: str, result: SyncResult) -> None:
        {
            SyncResult.CREATED: self.created,
            SyncResult.UPDATED: self.updated,
            SyncResult.UNCHANGED: self.unchanged,
        }[result].append(slug)


@dataclass
class ImportReport(Outcomes):
    """The outcomes of the documents; `profiles` holds those of the plant profiles."""

    dry_run: bool = False
    failed: dict[str, str] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)
    profiles: Outcomes = field(default_factory=Outcomes)

    @property
    def succeeded(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        line = (
            f"created={len(self.created)} updated={len(self.updated)} "
            f"unchanged={len(self.unchanged)} failed={len(self.failed)} stale={len(self.stale)} "
            f"profiles: created={len(self.profiles.created)} "
            f"updated={len(self.profiles.updated)} unchanged={len(self.profiles.unchanged)}"
        )
        return f"{line} (dry run, nothing written)" if self.dry_run else line


@dataclass(frozen=True)
class PlantDrafts:
    document: DocumentDraft
    profile: PlantProfile


class ImportRunner:
    """Fetches the listed plants and the known problems and stores each as a knowledge document.

    A plant also becomes a plant profile, stored in the same unit of work as its document. A plant
    or problem that cannot be fetched, converted or stored is reported and skipped; the rest go on.
    """

    def __init__(
        self,
        source: KnowledgeSource,
        unit_of_work: Callable[[], UnitOfWork],
        plants: Sequence[PlantEntry],
        today: Callable[[], date],
        dry_run: bool = False,
    ) -> None:
        self._source = source
        self._unit_of_work = unit_of_work
        self._plants = plants
        self._today = today
        self._dry_run = dry_run

    def run(self) -> ImportReport:
        """Raises SourceError when the shared relationships or problem list are unavailable."""
        relationships = parse_vulnerabilities(self._source.relationships())
        problems = self._problem_names(self._source.problems_index())
        report = ImportReport(dry_run=self._dry_run)
        plants = self._plant_drafts(relationships, problems, report)
        plant_names = {slug: drafts.document.species for slug, drafts in plants.items()}
        problem_documents = self._problem_documents(relationships, plant_names, problems, report)
        units = [(drafts.document, drafts.profile) for drafts in plants.values()]
        units += [(document, None) for document in problem_documents]
        if self._dry_run:
            self._compare(units, report)
        else:
            for document, profile in units:
                self._store(document, profile, report)
        planned = {f"{PLANT_PREFIX}{entry.source_slug}" for entry in self._plants}
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

    def _plant_drafts(
        self,
        relationships: Sequence[Relationship],
        problems: Mapping[str, str],
        report: ImportReport,
    ) -> dict[str, PlantDrafts]:
        drafts: dict[str, PlantDrafts] = {}
        for entry in self._plants:
            built = self._attempt(
                f"{PLANT_PREFIX}{entry.source_slug}",
                report,
                lambda entry=entry: self._convert_plant(entry, relationships, problems),
            )
            if built is not None:
                drafts[entry.source_slug] = built
        return drafts

    def _convert_plant(
        self, entry: PlantEntry, relationships: Sequence[Relationship], problems: Mapping[str, str]
    ) -> PlantDrafts:
        record = self._source.plant(entry.source_slug)
        today = self._today()
        return PlantDrafts(
            convert_plant(record, entry, relationships, problems, today),
            convert_profile(record, entry, today),
        )

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

    def _attempt[T](self, slug: str, report: ImportReport, build: Callable[[], T]) -> T | None:
        try:
            return build()
        except (SourceError, ConversionError) as error:
            self._fail(slug, str(error), report)
            return None

    def _store(
        self, document: DocumentDraft, profile: PlantProfile | None, report: ImportReport
    ) -> None:
        try:
            with self._unit_of_work() as uow:
                result = uow.knowledge.sync(document)
                profile_result = (
                    None if profile is None else uow.plant_profiles.sync_profile(profile)
                )
                uow.commit()
        except SQLAlchemyError as error:
            self._fail(document.slug, f"database error: {error.__class__.__name__}", report)
            return
        self._record(document.slug, result, report, "knowledge document synced")
        if profile is not None and profile_result is not None:
            self._record_profile(profile.slug, profile_result, report, "plant profile synced")

    def _compare(
        self, units: Sequence[tuple[DocumentDraft, PlantProfile | None]], report: ImportReport
    ) -> None:
        profiles = [profile for _, profile in units if profile is not None]
        with self._unit_of_work() as uow:
            stored = uow.knowledge.content_hashes([document.slug for document, _ in units])
            stored_profiles = uow.plant_profiles.content_hashes([p.slug for p in profiles])
        for document, profile in units:
            result = _compared(stored.get(document.slug), document.content_sha256)
            self._record(document.slug, result, report, "knowledge document compared")
            if profile is not None:
                profile_result = _compared(
                    stored_profiles.get(profile.slug), profile.content_sha256
                )
                self._record_profile(profile.slug, profile_result, report, "plant profile compared")

    def _report_stale(self, planned: set[str], report: ImportReport) -> None:
        with self._unit_of_work() as uow:
            stored = uow.knowledge.slugs_with_prefix(PLANT_PREFIX)
        report.stale = sorted(set(stored) - planned)
        for slug in report.stale:
            logger.warning("stored PlantSolve document is no longer imported", extra={"slug": slug})

    def _record(self, slug: str, result: SyncResult, report: ImportReport, message: str) -> None:
        report.record(slug, result)
        logger.info(message, extra={"slug": slug, "sync_result": result.value})

    def _record_profile(
        self, slug: str, result: SyncResult, report: ImportReport, message: str
    ) -> None:
        report.profiles.record(slug, result)
        logger.info(message, extra={"slug": slug, "sync_result": result.value})

    def _fail(self, slug: str, reason: str, report: ImportReport) -> None:
        report.failed[slug] = reason
        logger.error("knowledge document failed", extra={"slug": slug, "reason": reason})


def _compared(stored_hash: str | None, new_hash: str) -> SyncResult:
    if stored_hash is None:
        return SyncResult.CREATED
    return SyncResult.UNCHANGED if stored_hash == new_hash else SyncResult.UPDATED
