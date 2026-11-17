"""Lineups, per-game locks, the fantasy calendar and head-to-head matchups (Plan 3, 4.3-4.5).

`lineup_slot` is where each rostered player sits right now. `lineup_lock` is a snapshot written
when a player's game starts: it records the slot the player was in at puck drop, and only players
in a starting slot at that moment score for the team. Points are always worked out from the stored
box scores, never added up incrementally, so nothing here stores a running total except a
finished matchup's final result (as an exact fraction in text, because points such as 2/3 of a
win cannot be stored exactly as a decimal).
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from undertow.data import Base

LINEUP_SLOTS = ("F", "D", "UTIL", "G", "BN", "IR")
STARTING_SLOTS = ("F", "D", "UTIL", "G")
MATCHUP_STATUSES = ("scheduled", "live", "final", "finalized")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class FantasyWeek(Base):
    """One fantasy week of a real season: Monday to Sunday (Eastern dates), except that the
    opening weekend is merged into the first full week. Empty weeks (the All-Star break) are
    skipped, so `week_no` counts only playable weeks."""

    __tablename__ = "fantasy_week"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id", ondelete="CASCADE"))
    week_no: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # Real games scheduled in the week, league wide. Handy for spotting thin weeks.
    games: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("season_id", "week_no", name="uq_fantasy_week_season_no"),
        CheckConstraint("week_no >= 1 AND end_date >= start_date", name="week_sane"),
    )


class LineupSlot(Base):
    __tablename__ = "lineup_slot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("fantasy_team.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    slot: Mapped[str] = mapped_column(String(4))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("team_id", "player_id", name="uq_lineup_slot_team_player"),
        CheckConstraint(_in("slot", LINEUP_SLOTS), name="slot_valid"),
    )


class LineupLock(Base):
    """A team's slot for one player, frozen when that player's game started."""

    __tablename__ = "lineup_lock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("fantasy_team.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id", ondelete="CASCADE"))
    slot: Mapped[str] = mapped_column(String(4))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("team_id", "player_id", "game_id", name="uq_lineup_lock_team_player_game"),
        CheckConstraint(_in("slot", LINEUP_SLOTS), name="slot_valid"),
        Index("ix_lineup_lock_game_id", "game_id"),
    )


class Matchup(Base):
    """Two teams head to head. A regular-season matchup is one week (`last_week_no` equals
    `week_no`); a playoff round spans two weeks and its total is the sum over both."""

    __tablename__ = "matchup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id", ondelete="CASCADE"))
    week_no: Mapped[int] = mapped_column(Integer)
    last_week_no: Mapped[int] = mapped_column(Integer)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("fantasy_team.id", ondelete="CASCADE"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("fantasy_team.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(10), default="scheduled", server_default="scheduled")
    # Exact fractions as text ("33/10"), set only when the matchup is finalized.
    home_points: Mapped[str | None] = mapped_column(String(40))
    away_points: Mapped[str | None] = mapped_column(String(40))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("home_team_id <> away_team_id", name="two_teams"),
        CheckConstraint("week_no >= 1 AND last_week_no >= week_no", name="weeks_sane"),
        CheckConstraint(_in("status", MATCHUP_STATUSES), name="status_valid"),
        UniqueConstraint("league_id", "week_no", "home_team_id", name="uq_matchup_week_home"),
        UniqueConstraint("league_id", "week_no", "away_team_id", name="uq_matchup_week_away"),
        Index("ix_matchup_league_week", "league_id", "week_no"),
    )
