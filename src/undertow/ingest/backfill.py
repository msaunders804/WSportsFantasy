"""History-only loader for past seasons. Safe to run against production.

Unlike `replay` (which drives the worker jobs and is for a throwaway database), this never
touches current state. It does NOT: mark any season current, sync rosters, overwrite team
rows, or set a team or `active` status on players. New players found only in old box scores
are created `inactive` with no team. It refuses a season the database calls current, and any
season id the worker syncs (`INGEST_SEASON_IDS`).

    uv run python -m undertow.ingest.backfill --seasons 1,5,8 [--offline]

A target database that is not on localhost needs `--allow-remote`, and the host is printed
first so a wrong DATABASE_URL is visible before anything is written.
"""

import argparse
import logging
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, select

from undertow.config import get_settings
from undertow.data.models import Game, Season, ShotEvent, SkaterGameStats
from undertow.data.session import session_factory as default_session_factory
from undertow.ingest import store, sync
from undertow.ingest.feed import Feed
from undertow.ingest.parsing import parse_teams
from undertow.ingest.replay import CachingFeed, _try_live_feed, validate

log = logging.getLogger(__name__)


def backfill_history(session_factory, feed: Feed, season_ids: list[int]) -> dict[str, int]:
    _guard_seasons(session_factory, season_ids)

    with session_factory() as session:
        sync.sync_seasons(session, feed, current_season_id=None)  # None: is_current untouched
        session.commit()

    totals = {"games": 0, "box_scores": 0, "failed": 0}
    for season_id in season_ids:
        with session_factory() as session:
            _, payload = store.save_snapshot(session, feed.teams(season_id))
            store.ensure_teams(session, parse_teams(payload))
            sync.sync_schedule(session, feed, season_id)
            session.commit()
        with session_factory() as session:
            have = select(SkaterGameStats.game_id).distinct()
            todo = list(
                session.scalars(
                    select(Game.id).where(
                        Game.season_id == season_id,
                        Game.status == "final",
                        Game.id.not_in(have),
                    )
                )
            )
            totals["games"] += session.scalar(
                select(func.count()).select_from(Game).where(Game.season_id == season_id)
            )
        for game_id in todo:
            try:
                with session_factory() as session:
                    if sync.sync_game(session, feed, game_id, history_only=True):
                        totals["box_scores"] += 1
                    session.commit()
            except Exception:
                totals["failed"] += 1
                log.exception("box score failed for game %s", game_id)
    return totals


def _guard_seasons(session_factory, season_ids: list[int]) -> None:
    protected = set(get_settings().season_ids)
    if protected & set(season_ids):
        raise SystemExit(
            f"refusing seasons the worker syncs: {sorted(protected & set(season_ids))}"
        )
    with session_factory() as session:
        current = set(session.scalars(select(Season.id).where(Season.is_current)))
    if current & set(season_ids):
        raise SystemExit(f"refusing the current season: {sorted(current & set(season_ids))}")


def backfill_play_by_play(session_factory, feed: Feed, season_ids: list[int]) -> dict[str, int]:
    """Load shots and penalties for final games that have none yet. Same safety rules."""
    _guard_seasons(session_factory, season_ids)
    totals = {"play_by_play": 0, "failed": 0}
    for season_id in season_ids:
        with session_factory() as session:
            done = select(ShotEvent.game_id).distinct()
            todo = list(
                session.scalars(
                    select(Game.id).where(
                        Game.season_id == season_id,
                        Game.status == "final",
                        Game.id.not_in(done),
                    )
                )
            )
        for game_id in todo:
            try:
                with session_factory() as session:
                    if sync.sync_play_by_play(session, feed, game_id, history_only=True):
                        totals["play_by_play"] += 1
                    session.commit()
            except Exception:
                totals["failed"] += 1
                log.exception("play-by-play failed for game %s", game_id)
    return totals


def validate_play_by_play(session, season_id: int) -> list[str]:
    """Cross-check play-by-play against box scores. Empty list means they agree.

    Goals per team must equal skater goals per team (shootout goals are in neither). Shots on
    net per team are compared to box-score shots and reported separately by the caller, since
    the feed does not promise that they match.
    """
    problems: list[str] = []
    games = list(
        session.scalars(select(Game).where(Game.season_id == season_id, Game.status == "final"))
    )
    ids = [g.id for g in games]
    pbp_goals: dict[tuple[int, int], int] = defaultdict(int)
    for g, team, is_goal in session.execute(
        select(ShotEvent.game_id, ShotEvent.team_id, ShotEvent.is_goal).where(
            ShotEvent.game_id.in_(ids)
        )
    ):
        pbp_goals[(g, team)] += int(is_goal)
    box_goals: dict[tuple[int, int], int] = defaultdict(int)
    for g, team, goals in session.execute(
        select(SkaterGameStats.game_id, SkaterGameStats.team_id, SkaterGameStats.goals).where(
            SkaterGameStats.game_id.in_(ids)
        )
    ):
        box_goals[(g, team)] += goals
    have = {g for g, _ in pbp_goals}
    for game in games:
        if game.id not in have:
            problems.append(f"game {game.id}: final but no play-by-play stored")
            continue
        for team in (game.home_team_id, game.away_team_id):
            a, b = pbp_goals.get((game.id, team), 0), box_goals.get((game.id, team), 0)
            if a != b:
                problems.append(f"game {game.id} team {team}: pbp goals {a} vs box goals {b}")
    return problems


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines contain the API key
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--seasons", required=True, help="comma-separated HockeyTech season ids")
    ap.add_argument("--cache-dir", type=Path, default=Path("data/replay_cache"))
    ap.add_argument("--offline", action="store_true", help="use only cached responses")
    ap.add_argument("--pbp", action="store_true", help="also load play-by-play (shots, penalties)")
    ap.add_argument("--allow-remote", action="store_true", help="permit a non-localhost database")
    args = ap.parse_args()

    host = urlparse(get_settings().database_url).hostname or "?"
    print(f"Target database host: {host}")
    if host not in ("localhost", "127.0.0.1") and not args.allow_remote:
        raise SystemExit("remote database: re-run with --allow-remote if this is intended")

    seasons = [int(x) for x in args.seasons.split(",")]
    feed = CachingFeed(None if args.offline else _try_live_feed(), args.cache_dir)
    sf = default_session_factory()
    print("Result:", backfill_history(sf, feed, seasons))
    if args.pbp:
        print("Play-by-play:", backfill_play_by_play(sf, feed, seasons))
    with sf() as session:
        for sid in seasons:
            problems = validate(session, sid)
            print(f"Season {sid}: integrity problems = {len(problems)}")
            for p in problems[:20]:
                print("  ", p)
            if args.pbp:
                pp = validate_play_by_play(session, sid)
                print(f"Season {sid}: play-by-play problems = {len(pp)}")
                for p in pp[:20]:
                    print("  ", p)


if __name__ == "__main__":
    main()
