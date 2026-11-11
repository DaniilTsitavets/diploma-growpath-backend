import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

DB_TESTS_DIR = Path(__file__).parent / "db"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if DB_TESTS_DIR in item.path.parents:
            item.add_marker(pytest.mark.db)


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """`create_app` configures the root logger; a test must not change what the next one sees."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers = handlers
    root.setLevel(level)
