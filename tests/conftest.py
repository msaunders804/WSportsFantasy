import os

import pytest

# Tests never reach the real feed; DB tests need DATABASE_URL pointing at a throwaway database.
os.environ.setdefault("ENVIRONMENT", "test")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="DATABASE_URL not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)
