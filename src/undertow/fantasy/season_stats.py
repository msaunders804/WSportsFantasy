"""Past-season stats with the fantasy points they would have scored. Read-only, no HTTP.

Everything is scored with the standard table (`ScoringConfig()`), the same one every league
starts with, from final games only. Points are exact fractions and are only rounded for display
(`show`). Season totals are cached for a few minutes because they change only when a game is
corrected, and every visitor to the stats page asks for the same thing.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from undertow.data.models import (
    Game,
    GoalieGameStats,
    Player,
    ProTeam,
    Season,
    SkaterGameStats,
)
from undertow.fantasy import stat_lines
from undertow.fantasy.scoring import ScoringConfig, score_goalie, score_skater

CACHE_TTL_SECONDS = 600
SCORING = ScoringConfig()

SKATER_LABELS = {
    "goal": "Goals",
    "assist": "Assists",
    "pp_point": "Power-play points",
    "sh_point": "Short-handed points",
    "shot": "Shots on goal",
    "hit": "Hits",
    "block": "Blocked shots",
}
GOALIE_LABELS = {
    "win": "Regulation wins",
    "ot_win": "OT/shootout wins",
    "save": "Saves",
    "goal_against": "Goals against",
    "shutout": "Shutouts",
    "ot_loss": "OT/shootout losses",
}


def show(value: Fraction, places: int = 1) -> str:
    """A fraction as a rounded string for display only (half up), e.g. Fraction(33, 10) -> '3.3'."""
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return str(exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def plain(value: Fraction, places: int = 2) -> str:
    """Like `show` but without trailing zeros: 2, 0.5, 2.67. For rules and per-game numbers."""
    text = show(value, places)
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass
class SkaterSeason:
    player_id: int
    name: str
    pos: str
    team: str
    gp: int = 0
    goals: int = 0
    assists: int = 0
    shots: int = 0
    hits: int = 0
    blocks: int = 0
    pp_points: int = 0
    sh_points: int = 0
    pim: int = 0
    plus_minus: int = 0
    fantasy: Fraction = Fraction(0)
    breakdown: dict[str, Fraction] = field(default_factory=dict)

    @property
    def points(self) -> int:
        return self.goals + self.assists

    @property
    def fpg(self) -> Fraction:
        return self.fantasy / self.gp if self.gp else Fraction(0)


@dataclass
class GoalieSeason:
    player_id: int
    name: str
    pos: str
    team: str
    gp: int = 0
    wins: int = 0
    losses: int = 0  # regulation losses
    ot_losses: int = 0  # overtime and shootout losses
    shots_against: int = 0
    saves: int = 0
    goals_against: int = 0
    shutouts: int = 0
    fantasy: Fraction = Fraction(0)
    breakdown: dict[str, Fraction] = field(default_factory=dict)

    @property
    def fpg(self) -> Fraction:
        return self.fantasy / self.gp if self.gp else Fraction(0)

    @property
    def save_pct(self) -> str:
        return f"{self.saves / self.shots_against:.3f}" if self.shots_against else "-"


@dataclass
class SeasonStats:
    season_id: int
    season_name: str
    skaters: list[SkaterSeason]
    goalies: list[GoalieSeason]
    games: int  # final games the totals come from


@dataclass
class GameLine:
    game_id: int
    day: date
    opponent: str
    home: bool
    stats: dict[str, str]  # label -> value, in display order
    fantasy: Fraction
    breakdown: dict[str, Fraction]


def regular_seasons_with_stats(session: Session) -> list[tuple[int, str]]:
    """(id, name) of every regular season that has box scores, newest first. Seasons are
    recognised by the feed's name, since the feed does not flag preseason as such."""
    has_stats = select(Game.season_id).join(SkaterGameStats, SkaterGameStats.game_id == Game.id)
    rows = session.execute(
        select(Season.id, Season.name)
        .where(
            Season.is_playoff.is_(False),
            Season.name.ilike("%regular season%"),
            Season.id.in_(has_stats),
        )
        .order_by(Season.id.desc())
    ).all()
    return [(i, n) for i, n in rows]


_lock = threading.Lock()
_cache: dict[int, tuple[float, SeasonStats]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def season_stats(session: Session, season_id: int) -> SeasonStats | None:
    """Season totals for every player who played, or None if the season is unknown."""
    with _lock:
        hit = _cache.get(season_id)
        if hit is not None and time.monotonic() - hit[0] <= CACHE_TTL_SECONDS:
            return hit[1]
    result = _compute(session, season_id)
    if result is not None:
        with _lock:
            _cache[season_id] = (time.monotonic(), result)
    return result


def _add(into: dict[str, Fraction], breakdown: dict[str, Fraction]) -> None:
    for key, value in breakdown.items():
        into[key] = into.get(key, Fraction(0)) + value


def _compute(session: Session, season_id: int) -> SeasonStats | None:
    season = session.get(Season, season_id)
    if season is None:
        return None
    games = len(
        session.scalars(
            select(Game.id).where(Game.season_id == season_id, Game.status == "final")
        ).all()
    )
    skaters: dict[int, SkaterSeason] = {}
    rows = session.execute(
        select(SkaterGameStats, Player, ProTeam.code)
        .join(Game, Game.id == SkaterGameStats.game_id)
        .join(Player, Player.id == SkaterGameStats.player_id)
        .join(ProTeam, ProTeam.id == SkaterGameStats.team_id)
        .where(Game.season_id == season_id, Game.status == "final")
        .order_by(Game.start_utc, Game.id)  # so the last team seen is the latest one
    )
    for row, player, code in rows:
        if row.toi_seconds <= 0 or player.position not in ("F", "D"):
            continue
        s = skaters.get(player.id)
        if s is None:
            s = skaters[player.id] = SkaterSeason(
                player.id, f"{player.first_name} {player.last_name}".strip(), player.position, code
            )
        s.team = code
        s.gp += 1
        s.goals += row.goals
        s.assists += row.assists
        s.shots += row.shots
        s.hits += row.hits
        s.blocks += row.blocks
        s.pp_points += row.pp_goals + row.pp_assists
        s.sh_points += row.sh_goals + row.sh_assists
        s.pim += row.penalty_minutes
        s.plus_minus += row.plus_minus
        result = score_skater(stat_lines.skater_stat_line(row), SCORING)
        s.fantasy += result.total
        _add(s.breakdown, result.breakdown)

    goalies: dict[int, GoalieSeason] = {}
    grows = session.execute(
        select(GoalieGameStats, Player, ProTeam.code, Game.result_type)
        .join(Game, Game.id == GoalieGameStats.game_id)
        .join(Player, Player.id == GoalieGameStats.player_id)
        .join(ProTeam, ProTeam.id == GoalieGameStats.team_id)
        .where(Game.season_id == season_id, Game.status == "final")
        .order_by(Game.start_utc, Game.id)
    )
    for row, player, code, result_type in grows:
        line = stat_lines.goalie_stat_line(row, result_type)
        if not line.played:
            continue
        g = goalies.get(player.id)
        if g is None:
            g = goalies[player.id] = GoalieSeason(
                player.id, f"{player.first_name} {player.last_name}".strip(), "G", code
            )
        g.team = code
        g.gp += 1
        g.shots_against += row.shots_against
        g.saves += row.saves
        g.goals_against += row.goals_against
        g.shutouts += 1 if row.shutout else 0
        if row.decision == "W":
            g.wins += 1
        elif row.decision == "L":
            g.losses += 1
        elif row.decision in ("OTL", "SOL"):
            g.ot_losses += 1
        result = score_goalie(line, SCORING)
        g.fantasy += result.total
        _add(g.breakdown, result.breakdown)

    return SeasonStats(
        season_id=season_id,
        season_name=season.name,
        skaters=sorted(skaters.values(), key=lambda p: (-p.fantasy, p.name)),
        goalies=sorted(goalies.values(), key=lambda p: (-p.fantasy, p.name)),
        games=games,
    )


def player_summary(stats: SeasonStats, player_id: int) -> SkaterSeason | GoalieSeason | None:
    for p in stats.skaters:
        if p.player_id == player_id:
            return p
    for g in stats.goalies:
        if g.player_id == player_id:
            return g
    return None


def rank_of(stats: SeasonStats, player: SkaterSeason | GoalieSeason) -> tuple[int, int]:
    """(rank among all players, rank at the position) by total fantasy points."""
    everyone = sorted([*stats.skaters, *stats.goalies], key=lambda p: (-p.fantasy, p.name))
    overall = next(i for i, p in enumerate(everyone, 1) if p.player_id == player.player_id)
    same = [p for p in everyone if p.pos == player.pos]
    at_pos = next(i for i, p in enumerate(same, 1) if p.player_id == player.player_id)
    return overall, at_pos


def game_log(session: Session, player_id: int, season_id: int) -> list[GameLine]:
    """Every game the player played in a season, oldest first, with the points it scored."""
    home = aliased(ProTeam)
    away = aliased(ProTeam)
    log: list[GameLine] = []
    base = (
        select(Game, home.code, away.code)
        .join(home, home.id == Game.home_team_id)
        .join(away, away.id == Game.away_team_id)
    )
    skater_rows = session.execute(
        base.add_columns(SkaterGameStats)
        .join(SkaterGameStats, SkaterGameStats.game_id == Game.id)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            SkaterGameStats.player_id == player_id,
            SkaterGameStats.toi_seconds > 0,
        )
        .order_by(Game.start_utc, Game.id)
    )
    for game, home_code, away_code, row in skater_rows:
        is_home = row.team_id == game.home_team_id
        result = score_skater(stat_lines.skater_stat_line(row), SCORING)
        log.append(
            GameLine(
                game.id,
                game.start_utc.date(),
                away_code if is_home else home_code,
                is_home,
                {
                    "G": str(row.goals),
                    "A": str(row.assists),
                    "SOG": str(row.shots),
                    "Hits": str(row.hits),
                    "Blk": str(row.blocks),
                    "PPP": str(row.pp_goals + row.pp_assists),
                },
                result.total,
                result.breakdown,
            )
        )
    goalie_rows = session.execute(
        base.add_columns(GoalieGameStats)
        .join(GoalieGameStats, GoalieGameStats.game_id == Game.id)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            GoalieGameStats.player_id == player_id,
        )
        .order_by(Game.start_utc, Game.id)
    )
    for game, home_code, away_code, row in goalie_rows:
        line = stat_lines.goalie_stat_line(row, game.result_type)
        if not line.played:
            continue
        is_home = row.team_id == game.home_team_id
        result = score_goalie(line, SCORING)
        log.append(
            GameLine(
                game.id,
                game.start_utc.date(),
                away_code if is_home else home_code,
                is_home,
                {
                    "Dec": row.decision or "-",
                    "SA": str(row.shots_against),
                    "SV": str(row.saves),
                    "GA": str(row.goals_against),
                    "SO": "1" if row.shutout else "0",
                },
                result.total,
                result.breakdown,
            )
        )
    return log
