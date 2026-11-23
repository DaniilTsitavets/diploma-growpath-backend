import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from growpath_observability import configure_logging
from sqlalchemy.orm import sessionmaker

from app.db import create_engine_for_url
from app.knowledge_import.convert import ConversionError
from app.knowledge_import.label_export import export_catalog_labels
from app.knowledge_import.plants import read_plant_entries
from app.knowledge_import.run import ImportReport, ImportRunner
from app.knowledge_import.settings import FetchSettings, ImportSettings
from app.knowledge_import.source import PlantSolveSource, SourceError, create_http_client
from app.repository.unit_of_work import SqlAlchemyUnitOfWork

logger = logging.getLogger(__name__)

DEFAULT_PLANTS_FILE = Path(__file__).with_name("plantsolve_plants.txt")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.knowledge_import",
        description="Import PlantSolve open data (CC BY 4.0) into the care-chat knowledge base.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="compare with the database, write nothing"
    )
    parser.add_argument(
        "--plants-file",
        type=Path,
        default=DEFAULT_PLANTS_FILE,
        help="lines of `plantsolve-slug | our-slug | Species`",
    )
    parser.add_argument(
        "--pause", type=float, default=None, help="seconds between requests (default from settings)"
    )
    parser.add_argument(
        "--write-catalog-labels",
        type=Path,
        metavar="PATH",
        help="write the catalog labels of the listed plants as JSON to PATH; needs no database",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_catalog_labels is not None:
        return write_catalog_labels(args)
    settings = ImportSettings()
    configure_logging(settings.service_name, settings.log_level)
    plants = read_plant_entries(args.plants_file)
    engine = create_engine_for_url(settings.database_url)
    try:
        with create_http_client(settings.kb_import_timeout_seconds) as client:
            runner = ImportRunner(
                PlantSolveSource(client, settings.fetch_policy(args.pause)),
                partial(SqlAlchemyUnitOfWork, sessionmaker(engine)),
                plants,
                lambda: datetime.now(UTC).date(),
                args.dry_run,
            )
            report = runner.run()
    except SourceError as error:
        logger.error("knowledge import aborted", extra={"reason": str(error)})
        return 1
    finally:
        engine.dispose()
    log_report(report)
    print(report.summary())
    return 0 if report.succeeded else 1


def write_catalog_labels(args: argparse.Namespace) -> int:
    settings = FetchSettings()
    configure_logging(settings.service_name, settings.log_level)
    plants = read_plant_entries(args.plants_file)
    try:
        with create_http_client(settings.kb_import_timeout_seconds) as client:
            table = export_catalog_labels(
                PlantSolveSource(client, settings.fetch_policy(args.pause)),
                plants,
                lambda: datetime.now(UTC).date(),
                args.write_catalog_labels,
            )
    except (SourceError, ConversionError) as error:
        logger.error("catalog labels not written", extra={"reason": str(error)})
        return 1
    logger.info("catalog labels written", extra={"plants": len(table)})
    print(f"wrote the labels of {len(table)} plants to {args.write_catalog_labels}")
    return 0


def log_report(report: ImportReport) -> None:
    """The counters use `documents_*` keys: `created` is a reserved LogRecord attribute."""
    logger.info(
        "knowledge import finished",
        extra={
            "summary": report.summary(),
            "documents_created": len(report.created),
            "documents_updated": len(report.updated),
            "documents_unchanged": len(report.unchanged),
            "documents_failed": len(report.failed),
            "documents_stale": len(report.stale),
            "profiles_created": len(report.profiles.created),
            "profiles_updated": len(report.profiles.updated),
            "profiles_unchanged": len(report.profiles.unchanged),
        },
    )


if __name__ == "__main__":
    sys.exit(main())
