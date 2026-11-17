"""Sync jobs: fetch via the Feed, store a raw snapshot, then upsert normalized rows.

Each function takes a Session and commits nothing; the caller owns the transaction.
"""

import logging

from sqlalchemy.orm import Session

from undertow.data.models import Game
from undertow.ingest import store
from undertow.ingest.feed import Feed
from undertow.ingest.parsing import (
    parse_box_score,
    parse_play_by_play,
    parse_roster,
    parse_schedule,
    parse_seasons,
    parse_teams,
)

log = logging.getLogger(__name__)


def sync_seasons(session: Session, feed: Feed, current_season_id: int | None = None) -> None:
    _, payload = store.save_snapshot(session, feed.seasons())
    store.upsert_seasons(session, parse_seasons(payload), current_season_id)


def sync_teams(session: Session, feed: Feed, season_id: int) -> list[int]:
    _, payload = store.save_snapshot(session, feed.teams(season_id))
    teams = parse_teams(payload)
    store.upsert_teams(session, teams)
    return [t.id for t in teams]


def sync_roster(session: Session, feed: Feed, team_id: int, season_id: int) -> int:
    _, payload = store.save_snapshot(session, feed.roster(team_id, season_id))
    players = parse_roster(payload)
    store.upsert_roster_players(session, players)
    return len(players)


def sync_schedule(session: Session, feed: Feed, season_id: int) -> int:
    snap, payload = store.save_snapshot(session, feed.schedule(season_id))
    if snap is None:
        return 0  # unchanged since last fetch
    games = parse_schedule(payload)
    store.upsert_games(session, games)
    return len(games)


def sync_game(session: Session, feed: Feed, game_id: int, history_only: bool = False) -> bool:
    """Fetch and store one box score. Returns False if the game is not in the schedule."""
    if session.get(Game, game_id) is None:
        log.warning("skipping box score for unknown game %s", game_id)
        return False
    _, payload = store.save_snapshot(session, feed.game_summary(game_id))
    store.upsert_box_score(session, parse_box_score(payload), history_only=history_only)
    return True


def sync_play_by_play(
    session: Session, feed: Feed, game_id: int, history_only: bool = False
) -> bool:
    """Fetch and store one game's shots and penalties. False if the game is not in the schedule."""
    if session.get(Game, game_id) is None:
        log.warning("skipping play-by-play for unknown game %s", game_id)
        return False
    _, payload = store.save_snapshot(session, feed.play_by_play(game_id))
    store.upsert_play_by_play(session, parse_play_by_play(payload, game_id), history_only)
    return True
