from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from undertow.db import get_engine
from undertow.ingest.feed import FixtureFeed

FIXTURES = Path(__file__).parent.parent / "fixtures" / "hockeytech"

TABLES = "skater_game_stats, goalie_game_stats, game, player, pro_team, season, raw_snapshot"


@pytest.fixture
def feed() -> FixtureFeed:
    return FixtureFeed(
        FIXTURES,
        {
            "seasons": "seasons.json",
            "teams:11": "teams_2026-27.json",
            "schedule:8": "schedule_2025-26.json",
            "schedule:11": "schedule_2026-27.json",
            "roster:1:8": "roster_bos_2025-26.json",
            "roster:1:10": "roster_bos_2026-27-pre.json",
            "game:210": "gamesummary_210_reg.json",
            "game:227": "gamesummary_227_so.json",
        },
    )


@pytest.fixture
def session():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    with Session(engine) as s:
        yield s
        s.rollback()
