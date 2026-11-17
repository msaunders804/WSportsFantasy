"""Default player rankings for the draft and the Players tab (Plan 4.6).

Fantasy points per game come from the stored box scores of the most recent finished regular
season, scored with the standard scoring table (`ScoringConfig()`), then valued by the same model
the demo draft uses (`draft.valuations`): shrunk toward the position average so a short hot
streak does not top the list, then measured against a replacement-level player.

This is a rough guide, and the UI must say so. Players with no history (rookies, expansion
teams) are valued at the position average. The commissioner cannot adjust ranks yet.
"""

from dataclasses import dataclass
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import (
    Game,
    GoalieGameStats,
    Player,
    ProTeam,
    Season,
    SkaterGameStats,
)
from undertow.fantasy import draft, stat_lines
from undertow.fantasy.scoring import ScoringConfig, score_goalie, score_skater


@dataclass(frozen=True)
class RankedPlayer:
    player: draft.DraftPlayer
    valuation: draft.Valuation


def history_season_ids(session: Session, before_season_id: int | None = None) -> list[int]:
    """The latest finished regular season with box scores before the season being drafted, as a
    one-item list (or empty).

    "Before" is `before_season_id`, or the current season if not given. Only seasons the feed
    names "Regular Season" count, so a preseason (which the feed does not flag as a playoff) or a
    playoff run can never stand in for a full season, and the result does not change by itself
    once the new season's games start.
    """
    if before_season_id is None:
        before_season_id = session.scalar(
            select(Season.id).where(Season.is_current).order_by(Season.id.desc()).limit(1)
        )
    has_stats = select(Game.season_id).join(SkaterGameStats, SkaterGameStats.game_id == Game.id)
    query = select(Season.id).where(
        Season.is_playoff.is_(False),
        Season.name.ilike("%regular season%"),
        Season.id.in_(has_stats),
    )
    if before_season_id is not None:
        query = query.where(Season.id < before_season_id)
    row = session.scalar(query.order_by(Season.id.desc()).limit(1))
    return [] if row is None else [row]


def points_by_player(
    session: Session, season_ids: list[int], config: ScoringConfig | None = None
) -> dict[int, tuple[Fraction, int]]:
    """{player id: (total fantasy points, games played)} over final games in `season_ids`."""
    config = config or ScoringConfig()
    totals: dict[int, tuple[Fraction, int]] = {}
    if not season_ids:
        return totals

    def add(player_id: int, points: Fraction) -> None:
        total, games = totals.get(player_id, (Fraction(0), 0))
        totals[player_id] = (total + points, games + 1)

    skaters = session.execute(
        select(SkaterGameStats)
        .join(Game, Game.id == SkaterGameStats.game_id)
        .where(Game.season_id.in_(season_ids), Game.status == "final")
    ).scalars()
    for row in skaters:
        if row.toi_seconds > 0:
            add(row.player_id, score_skater(stat_lines.skater_stat_line(row), config).total)
    goalies = session.execute(
        select(GoalieGameStats, Game.result_type)
        .join(Game, Game.id == GoalieGameStats.game_id)
        .where(Game.season_id.in_(season_ids), Game.status == "final")
    )
    for row, result_type in goalies:
        line = stat_lines.goalie_stat_line(row, result_type)
        if line.played:
            add(row.player_id, score_goalie(line, config).total)
    return totals


def draft_pool(session: Session, season_ids: list[int] | None = None) -> list[draft.DraftPlayer]:
    """Every active player with a position, with last season's line (zeros if none)."""
    if season_ids is None:
        season_ids = history_season_ids(session)
    points = points_by_player(session, season_ids)
    rows = session.execute(
        select(Player, ProTeam.code)
        .outerjoin(ProTeam, ProTeam.id == Player.pro_team_id)
        .where(Player.status == "active", Player.position.is_not(None))
        .order_by(Player.id)
    ).all()
    pool = []
    for player, code in rows:
        total, games = points.get(player.id, (Fraction(0), 0))
        pool.append(
            draft.DraftPlayer(
                id=player.id,
                name=f"{player.first_name} {player.last_name}".strip(),
                pos=player.position,
                team=code or "",
                fpg=float(total / games) if games else 0.0,
                gp=games,
                total=float(total),
            )
        )
    return pool


def rank_players(
    session: Session, cfg: draft.DraftConfig | None = None, season_ids: list[int] | None = None
) -> list[RankedPlayer]:
    """The pool best first by value over replacement. Ties break on player id, so the order
    never changes between calls on the same data."""
    pool = draft_pool(session, season_ids)
    values = draft.valuations(pool, cfg or draft.DraftConfig())
    ranked = sorted(pool, key=lambda p: (values[p.id].rank, p.id))
    return [RankedPlayer(p, values[p.id]) for p in ranked]


def default_ranking(session: Session) -> list[int]:
    """Player ids best first. Matches `draft_room.Ranking`, so it can be passed to autopick."""
    return [r.player.id for r in rank_players(session)]
