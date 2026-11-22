import logging

import pytest

from app.knowledge_import.__main__ import log_report
from app.knowledge_import.run import ImportReport


def test_the_summary_is_logged_with_its_counters(caplog: pytest.LogCaptureFixture) -> None:
    report = ImportReport(
        created=["plantsolve-a", "plantsolve-b"],
        updated=["plantsolve-c"],
        unchanged=[],
        failed={"plantsolve-d": "bad licence"},
        stale=["plantsolve-e"],
    )
    report.profiles.created.extend(["a", "b", "c"])
    report.profiles.unchanged.append("d")

    with caplog.at_level(logging.INFO, logger="app.knowledge_import.__main__"):
        log_report(report)

    record = caplog.records[-1]
    assert record.message == "knowledge import finished"
    assert record.__dict__["summary"] == report.summary()
    assert [
        record.__dict__[key]
        for key in (
            "documents_created",
            "documents_updated",
            "documents_unchanged",
            "documents_failed",
            "documents_stale",
            "profiles_created",
            "profiles_updated",
            "profiles_unchanged",
        )
    ] == [2, 1, 0, 1, 1, 3, 0, 1]
