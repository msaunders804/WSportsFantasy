"""Pure parsers and a replace-all writer for the private `research` NCAA tables.

Source: ncaa.com data via the henrygd/ncaa-api wrapper (scoreboard and box-score JSON). The raw
responses stay in local caches; only these clean rows go to the database. See docs/modeling.md.
"""

from datetime import date
from typing import Any

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from undertow.data.models import DraftPick, NcaaGame, NcaaPlayerGame, TranslationTraining


def season_label(d: date) -> str:
    """Season an NCAA date belongs to: Aug-Dec 2023 and Jan-Jul 2024 are both '2023-24'."""
    y = d.year if d.month >= 8 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def _i(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _i_or_none(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_scoreboard_game(game: dict, game_date: date) -> dict | None:
    """One `games[].game` object from a scoreboard response, or None if it is not a final."""
    if not str(game.get("finalMessage") or "").upper().startswith("FINAL"):
        return None
    return {
        "game_id": int(game["gameID"]),
        "season": season_label(game_date),
        "game_date": game_date,
        "home_slug": game["home"]["names"]["seo"],
        "away_slug": game["away"]["names"]["seo"],
        "home_score": _i_or_none(game["home"].get("score")),
        "away_score": _i_or_none(game["away"].get("score")),
    }


def parse_player_rows(box: dict, game_id: int) -> list[dict]:
    """Player lines from a `/game/{id}/boxscore` response, one dict per player on each team."""
    slugs = {str(t["teamId"]): t["seoname"] for t in box.get("teams", [])}
    rows = []
    for tb in box.get("teamBoxscore", []):
        slug = slugs.get(str(tb.get("teamId")))
        if slug is None:
            continue
        for p in tb.get("playerStats", []):
            rows.append(
                {
                    "game_id": game_id,
                    "team_slug": slug,
                    "first_name": (p.get("firstName") or "").strip()[:60],
                    "last_name": (p.get("lastName") or "").strip()[:60],
                    "position": (p.get("position") or "")[:4],
                    "number": _i_or_none(p.get("number")),
                    "participated": bool(p.get("participated")),
                    "starter": bool(p.get("starter")),
                    "shots": _i(p.get("shots")),
                    "goals": _i(p.get("goals")),
                    "assists": _i(p.get("assists")),
                    "blocks": _i(p.get("blk")),
                    "pp_goals": _i(p.get("powerPlayGoals")),
                    "sh_goals": _i(p.get("shortHandedGoals")),
                    "gw_goals": _i(p.get("gameWinningGoals")),
                    "minors": _i(p.get("minor")),
                    "majors": _i(p.get("major")),
                    "faceoffs_won": _i(p.get("facewon")),
                    "faceoffs_lost": _i(p.get("facelost")),
                    "goalie_minutes": _i(p.get("goalieMinutes")),
                    "goals_allowed": _i(p.get("goalsAllowed")),
                    "saves": _i(p.get("saves")),
                }
            )
    return rows


def _insert_batches(session: Session, model, rows: list[dict], size: int = 2000) -> None:
    for i in range(0, len(rows), size):
        session.execute(insert(model), rows[i : i + size])


def replace_research_tables(
    session: Session,
    games: list[dict],
    player_rows: list[dict],
    picks: list[dict],
    training: list[dict],
) -> dict[str, int]:
    """Replace every research table's contents (they are fully derived from local caches).

    The caller owns the transaction. Children are deleted before parents.
    """
    for model in (TranslationTraining, NcaaPlayerGame, NcaaGame, DraftPick):
        session.execute(delete(model))
    _insert_batches(session, NcaaGame, games)
    _insert_batches(session, NcaaPlayerGame, player_rows)
    _insert_batches(session, DraftPick, picks)
    _insert_batches(session, TranslationTraining, training)
    return {
        "ncaa_game": len(games),
        "ncaa_player_game": len(player_rows),
        "draft_pick": len(picks),
        "translation_training": len(training),
    }
