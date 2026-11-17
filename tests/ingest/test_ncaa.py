from datetime import date

import pytest
from sqlalchemy import func, select

from undertow.data.models import DraftPick, NcaaGame, NcaaPlayerGame
from undertow.ingest import ncaa


def test_season_label_spans_the_new_year():
    assert ncaa.season_label(date(2023, 10, 5)) == "2023-24"
    assert ncaa.season_label(date(2024, 3, 17)) == "2023-24"
    assert ncaa.season_label(date(2024, 9, 28)) == "2024-25"


def scoreboard_game(final="FINAL"):
    return {
        "gameID": "6497186",
        "finalMessage": final,
        "home": {"score": "11", "names": {"seo": "penn-st"}},
        "away": {"score": "2", "names": {"seo": "syracuse"}},
    }


def test_scoreboard_game_final_only():
    row = ncaa.parse_scoreboard_game(scoreboard_game(), date(2025, 11, 15))
    assert row == {
        "game_id": 6497186,
        "season": "2025-26",
        "game_date": date(2025, 11, 15),
        "home_slug": "penn-st",
        "away_slug": "syracuse",
        "home_score": 11,
        "away_score": 2,
    }
    assert ncaa.parse_scoreboard_game(scoreboard_game("3rd 10:00"), date(2025, 11, 15)) is None


def box():
    skater = {
        "firstName": " NICOLE",
        "lastName": "HALL",
        "position": "LW",
        "number": 21,
        "shots": "3",
        "goals": "1",
        "assists": "1",
        "blk": "0",
        "powerPlayGoals": "1",
        "participated": True,
        "starter": True,
    }
    goalie = {
        "firstName": "KATIE",
        "lastName": "DESA",
        "position": "G",
        "number": 35,
        "goalieMinutes": "60",
        "goalsAllowed": "2",
        "saves": "12",
        "participated": True,
        "starter": True,
    }
    return {
        "teams": [{"teamId": "1", "seoname": "penn-st"}, {"teamId": "2", "seoname": "syracuse"}],
        "teamBoxscore": [
            {"teamId": 1, "playerStats": [skater, goalie]},
            {"teamId": 2, "playerStats": [{**skater, "lastName": "OTHER", "number": 9}]},
        ],
    }


def test_player_rows_clean_names_and_numbers():
    rows = ncaa.parse_player_rows(box(), 6497186)
    assert len(rows) == 3
    hall = rows[0]
    assert (hall["first_name"], hall["team_slug"], hall["goals"], hall["pp_goals"]) == (
        "NICOLE",  # leading space stripped
        "penn-st",
        1,
        1,
    )
    assert (
        rows[1]["saves"] == 12 and rows[1]["goals_allowed"] == 2 and rows[1]["goalie_minutes"] == 60
    )
    assert rows[2]["team_slug"] == "syracuse"


@pytest.mark.db
def test_replace_is_idempotent(session):
    from sqlalchemy import text

    session.execute(text("DELETE FROM research.translation_training"))
    games = [ncaa.parse_scoreboard_game(scoreboard_game(), date(2025, 11, 15))]
    players = ncaa.parse_player_rows(box(), 6497186)
    picks = [
        {
            "draft_year": 2026,
            "pick": 1,
            "round": 1,
            "drafted_by": "Vancouver Goldeneyes",
            "player": "Caroline Harvey",
            "position": "D",
            "school_or_club": "Wisconsin",
            "league": "NCAA",
        }
    ]
    for _ in range(2):
        counts = ncaa.replace_research_tables(session, games, players, picks, [])
        session.flush()
    assert counts == {
        "ncaa_game": 1,
        "ncaa_player_game": 3,
        "draft_pick": 1,
        "translation_training": 0,
    }
    for model, n in ((NcaaGame, 1), (NcaaPlayerGame, 3), (DraftPick, 1)):
        assert session.scalar(select(func.count()).select_from(model)) == n
