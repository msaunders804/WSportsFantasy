"""Season replay: run a past season through ingestion, scoring and integrity checks.

Responses are cached on disk (`CachingFeed`), so the first run fetches from the live feed
(throttled) and every later run is offline and deterministic. A simulated clock drives the
real worker jobs (daily sync, live poll, reconcile), so this exercises the same code paths as
production.

    uv run python -m undertow.ingest.replay --season 8 --cache-dir data/replay_cache
"""

import argparse
import logging
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import Game, GoalieGameStats, Player, SkaterGameStats
from undertow.data.session import session_factory as default_session_factory
from undertow.fantasy.scoring import ScoringConfig, score_goalie, score_skater
from undertow.fantasy.stat_lines import goalie_stat_line, skater_stat_line
from undertow.ingest import jobs
from undertow.ingest.feed import Feed, HockeyTechFeed, RawResponse

log = logging.getLogger(__name__)

LOSS_DECISION_BY_RESULT = {"REG": "L", "OT": "OTL", "SO": "SOL"}


class CachingFeed:
    """Wraps a Feed; stores each response under `cache_dir` and serves it from there next time."""

    def __init__(self, inner: Feed | None, cache_dir: Path) -> None:
        self._inner = inner
        self._dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _cached(self, name: str, fetch) -> RawResponse:
        path = self._dir / f"{name}.json"
        meta = self._dir / f"{name}.url"
        if path.exists():
            return RawResponse(source_url=meta.read_text(), body=path.read_text())
        if self._inner is None:
            raise RuntimeError(f"{name} is not cached and no live feed is configured")
        raw = fetch()
        path.write_text(raw.body)
        meta.write_text(raw.source_url)
        return raw

    def seasons(self) -> RawResponse:
        return self._cached("seasons", lambda: self._inner.seasons())

    def teams(self, season_id: int) -> RawResponse:
        return self._cached(f"teams_{season_id}", lambda: self._inner.teams(season_id))

    def schedule(self, season_id: int) -> RawResponse:
        return self._cached(f"schedule_{season_id}", lambda: self._inner.schedule(season_id))

    def roster(self, team_id: int, season_id: int) -> RawResponse:
        return self._cached(
            f"roster_{team_id}_{season_id}", lambda: self._inner.roster(team_id, season_id)
        )

    def game_summary(self, game_id: int) -> RawResponse:
        return self._cached(f"game_{game_id}", lambda: self._inner.game_summary(game_id))


def run_replay(session_factory, feed: Feed, season_id: int) -> None:
    """Drive the worker jobs through the season on a simulated clock."""
    jobs.daily_sync(session_factory, feed, [season_id])
    with session_factory() as session:
        starts = sorted(session.scalars(select(Game.start_utc).where(Game.season_id == season_id)))
    for start in dict.fromkeys(starts):  # distinct, in order
        # Two hours after puck drop: inside the live window, and the game is over.
        jobs.poll_live(session_factory, feed, now=start + timedelta(hours=2))
    last = starts[-1] + timedelta(hours=2)
    # Reconcile passes at +7 h and +25 h after the last game (each game is final by then).
    jobs.reconcile_finals(session_factory, feed, now=last + timedelta(days=2))
    jobs.reconcile_finals(session_factory, feed, now=last + timedelta(days=3))


def validate(session: Session, season_id: int, expect_complete: bool = True) -> list[str]:
    """Cross-check stored stats against the schedule. Returns a list of problems (empty = clean)."""
    problems: list[str] = []
    games = list(
        session.scalars(select(Game).where(Game.season_id == season_id, Game.status == "final"))
    )
    skater_goals: dict[tuple[int, int], int] = defaultdict(int)
    empty_net: dict[tuple[int, int], int] = defaultdict(int)
    for row in session.scalars(
        select(SkaterGameStats).where(SkaterGameStats.game_id.in_([g.id for g in games]))
    ):
        skater_goals[(row.game_id, row.team_id)] += row.goals
        empty_net[(row.game_id, row.team_id)] += row.en_goals
    goalie_rows: dict[int, list[GoalieGameStats]] = defaultdict(list)
    for row in session.scalars(
        select(GoalieGameStats).where(GoalieGameStats.game_id.in_([g.id for g in games]))
    ):
        goalie_rows[row.game_id].append(row)

    for g in games:
        rows = goalie_rows.get(g.id, [])
        if not rows:
            if expect_complete:
                problems.append(f"game {g.id}: final but no box score stored")
            continue
        so = g.result_type == "SO"
        winner_home = g.home_goals > g.away_goals
        for team_id, goals in ((g.home_team_id, g.home_goals), (g.away_team_id, g.away_goals)):
            expected = goals - (1 if so and ((team_id == g.home_team_id) == winner_home) else 0)
            if skater_goals[(g.id, team_id)] != expected:
                got = skater_goals[(g.id, team_id)]
                problems.append(f"game {g.id}: team {team_id} skater goals {got} != {expected}")
            # Opposing goalies' goals against = this team's goals, minus shootout and empty-net
            # goals (a pulled goalie is not charged with those).
            charged = expected - empty_net[(g.id, team_id)]
            conceded = sum(r.goals_against for r in rows if r.team_id != team_id)
            if conceded != charged:
                problems.append(f"game {g.id}: opposing goalies conceded {conceded} != {charged}")
        decisions = [r.decision for r in rows if r.decision]
        if decisions.count("W") != 1:
            problems.append(f"game {g.id}: {decisions.count('W')} winning goalies")
        loss = LOSS_DECISION_BY_RESULT.get(g.result_type or "REG")
        if decisions.count(loss) != 1:
            problems.append(f"game {g.id}: expected one {loss} decision, got {decisions}")
    return problems


def leaderboard(
    session: Session, season_id: int, config: ScoringConfig | None = None, top: int = 15
):
    """Fantasy totals per player over the season (exact Fractions), best first."""
    config = config or ScoringConfig()
    result_by_game = dict(
        session.execute(select(Game.id, Game.result_type).where(Game.season_id == season_id)).all()
    )
    totals: dict[int, list] = defaultdict(lambda: [0, 0])  # player -> [points, games]
    for row in session.scalars(
        select(SkaterGameStats).where(SkaterGameStats.game_id.in_(result_by_game))
    ):
        totals[row.player_id][0] += score_skater(skater_stat_line(row), config).total
        totals[row.player_id][1] += 1
    for row in session.scalars(
        select(GoalieGameStats).where(GoalieGameStats.game_id.in_(result_by_game))
    ):
        if row.toi_seconds > 0:
            line = goalie_stat_line(row, result_by_game[row.game_id])
            totals[row.player_id][0] += score_goalie(line, config).total
            totals[row.player_id][1] += 1
    names = {p.id: p for p in session.scalars(select(Player).where(Player.id.in_(totals)))}
    ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)
    return [
        (names[pid].first_name + " " + names[pid].last_name, names[pid].position, gp, pts)
        for pid, (pts, gp) in ranked[:top]
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs full request URLs at INFO, which would put the API key in the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--cache-dir", type=Path, default=Path("data/replay_cache"))
    ap.add_argument("--offline", action="store_true", help="use only cached responses")
    args = ap.parse_args()

    live = None if args.offline else _try_live_feed()
    feed = CachingFeed(live, args.cache_dir)
    sf = default_session_factory()
    run_replay(sf, feed, args.season)
    with sf() as session:
        problems = validate(session, args.season)
        board = leaderboard(session, args.season)
    print(f"\nIntegrity problems: {len(problems)}")
    for p in problems[:50]:
        print("  ", p)
    print("\nTop fantasy scorers (default scoring):")
    for name, pos, gp, pts in board:
        print(f"  {name:28} {pos}  GP {gp:3}  {float(pts):7.2f}  ({gp and float(pts) / gp:.2f}/g)")


def _try_live_feed() -> Feed | None:
    try:
        return HockeyTechFeed()
    except Exception as exc:
        log.warning("no live feed (%s); cached responses only", exc)
        return None


if __name__ == "__main__":
    main()
