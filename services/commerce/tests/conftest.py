from pathlib import Path

import pytest

DB_TESTS_DIR = Path(__file__).parent / "db"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if DB_TESTS_DIR in item.path.parents:
            item.add_marker(pytest.mark.db)
