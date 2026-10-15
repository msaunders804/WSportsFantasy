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


FANTASY_TABLES = (
    "audit_log, auth_session, fantasy_team, league_member, league_settings, league, "
    "user_account, season"
)


@pytest.fixture
def fantasy_db():
    """A clean fantasy schema with one current season (id 11). Needs DATABASE_URL."""
    from sqlalchemy import text

    from undertow.data import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {FANTASY_TABLES} RESTART IDENTITY CASCADE"))
        conn.execute(
            text(
                "INSERT INTO season (id, name, is_playoff, is_current) "
                "VALUES (11, '2026-27', false, true)"
            )
        )
    return engine
