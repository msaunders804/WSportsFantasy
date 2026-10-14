"""Worker jobs. Each takes a session factory, a Feed and an injectable `now` (so tests and
season replay can drive a simulated clock) and returns a short detail string.

Per-game failures are logged and skipped so one bad box score never blocks the rest.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from undertow.ingest import sync
from undertow.ingest.feed import Feed
from undertow.models import Game, JobRun

log = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

# A game is polled from just before puck drop until this long after, unless it goes final.
LIVE_WINDOW_BEFORE = timedelta(minutes=10)
LIVE_WINDOW_AFTER = timedelta(hours=5)
# Follow-up fetches after a game first reports final: +6 h then +24 h.
RECONCILE_DELAYS = (timedelta(hours=6), timedelta(hours=24))
RESYNC_LOOKBACK = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(UTC)


def _fetch_games(
    session_factory: SessionFactory, feed: Feed, game_ids: list[int], now: datetime
) -> int:
    """Fetch each box score in its own transaction; stamp `final_at` the first time one is final."""
    done = 0
    for game_id in game_ids:
        try:
            with session_factory() as session:
                if not sync.sync_game(session, feed, game_id):
                    continue
                game = session.get(Game, game_id)
                if game.status == "final" and game.final_at is None:
                    game.final_at = now
                session.commit()
                done += 1
        except Exception:
            log.exception("box score fetch failed for game %s", game_id)
    return done


def daily_sync(session_factory: SessionFactory, feed: Feed, season_ids: list[int]) -> str:
    """Seasons, teams, schedule and rosters (signings, releases, position changes)."""
    with session_factory() as session:
        sync.sync_seasons(session, feed, current_season_id=season_ids[-1] if season_ids else None)
        session.commit()
    games = players = 0
    for season_id in season_ids:
        with session_factory() as session:
            team_ids = sync.sync_teams(session, feed, season_id)
            games += sync.sync_schedule(session, feed, season_id)
            session.commit()
        for team_id in team_ids:
            try:
                with session_factory() as session:
                    players += sync.sync_roster(session, feed, team_id, season_id)
                    session.commit()
            except Exception:
                log.exception("roster sync failed for team %s season %s", team_id, season_id)
    return f"games={games} roster_players={players}"


def live_game_ids(session: Session, now: datetime) -> list[int]:
    return list(
        session.scalars(
            select(Game.id).where(
                # Also games the schedule calls final but whose box score we have not captured yet.
                or_(Game.status != "final", Game.final_at.is_(None)),
                Game.start_utc <= now + LIVE_WINDOW_BEFORE,
                Game.start_utc >= now - LIVE_WINDOW_AFTER,
            )
        )
    )


def poll_live(session_factory: SessionFactory, feed: Feed, now: datetime | None = None) -> str:
    """Fetch box scores for games in their live window. No-op (no requests) when none are."""
    now = now or _now()
    with session_factory() as session:
        ids = live_game_ids(session, now)
    if not ids:
        return "no live games"
    return f"polled={_fetch_games(session_factory, feed, ids, now)}/{len(ids)}"


def reconcile_finals(
    session_factory: SessionFactory, feed: Feed, now: datetime | None = None
) -> str:
    """Re-fetch games +6 h and +24 h after they first went final, to catch scorer changes."""
    now = now or _now()
    with session_factory() as session:
        due = [
            g.id
            for g in session.scalars(
                select(Game).where(
                    Game.final_at.is_not(None), Game.reconcile_count < len(RECONCILE_DELAYS)
                )
            )
            if now >= g.final_at + RECONCILE_DELAYS[g.reconcile_count]
        ]
    fetched = 0
    for game_id in due:
        n = _fetch_games(session_factory, feed, [game_id], now)
        if n:
            with session_factory() as session:
                session.get(Game, game_id).reconcile_count += 1
                session.commit()
            fetched += n
    return f"reconciled={fetched}/{len(due)}"


def nightly_resync(
    session_factory: SessionFactory, feed: Feed, season_ids: list[int], now: datetime | None = None
) -> str:
    """Refresh the schedule, then re-fetch the past week of games to pick up stat corrections."""
    now = now or _now()
    for season_id in season_ids:
        with session_factory() as session:
            sync.sync_schedule(session, feed, season_id)
            session.commit()
    with session_factory() as session:
        ids = list(
            session.scalars(
                select(Game.id).where(
                    Game.start_utc >= now - RESYNC_LOOKBACK,
                    Game.start_utc <= now,
                    Game.season_id.in_(season_ids),
                )
            )
        )
    return f"resynced={_fetch_games(session_factory, feed, ids, now)}/{len(ids)}"


def run_job(session_factory: SessionFactory, name: str, fn: Callable[[], str]) -> bool:
    """Run a job, record the outcome in `job_run`, never raise (the scheduler keeps going)."""
    started = _now()
    ok, detail = True, ""
    try:
        detail = fn()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
        log.exception("job %s failed", name)
    try:
        with session_factory() as session:
            session.add(
                JobRun(
                    job=name, started_at=started, finished_at=_now(), ok=ok, detail=detail[:1000]
                )
            )
            session.commit()
    except Exception:
        log.exception("could not record job_run for %s", name)
    return ok
