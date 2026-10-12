"""Real-hockey tables. Primary keys are the HockeyTech IDs (stable integers)."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from undertow.db import Base


class Season(Base):
    __tablename__ = "season"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_playoff: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class ProTeam(Base):
    __tablename__ = "pro_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(100), default="")
    nickname: Mapped[str] = mapped_column(String(100), default="")
    conference: Mapped[str | None] = mapped_column(String(10))
    logo_url: Mapped[str] = mapped_column(String(300), default="")


class Player(Base):
    __tablename__ = "player"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    person_id: Mapped[int | None] = mapped_column(Integer)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    # F, D or G. Null only if the feed gives an unmapped position (logged by the parser).
    position: Mapped[str | None] = mapped_column(String(1))
    raw_position: Mapped[str] = mapped_column(String(10), default="")
    pro_team_id: Mapped[int | None] = mapped_column(ForeignKey("pro_team.id"))
    jersey: Mapped[int | None] = mapped_column(Integer)
    # Signing status from the feed (Signed, Camp Invitee, ...). Injury/LTIR is not in the feed yet.
    feed_status: Mapped[str] = mapped_column(String(50), default="")
    # Our own availability flag: active / injured / ltir / inactive.
    status: Mapped[str] = mapped_column(String(20), default="active")
    birthdate: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_player_pro_team_id", "pro_team_id"),)


class Game(Base):
    __tablename__ = "game"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("pro_team.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("pro_team.id"))
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))  # scheduled / live / final
    status_text: Mapped[str] = mapped_column(String(50), default="")
    result_type: Mapped[str | None] = mapped_column(String(3))  # REG / OT / SO, once final
    home_goals: Mapped[int] = mapped_column(Integer, default=0)
    away_goals: Mapped[int] = mapped_column(Integer, default=0)
    venue_name: Mapped[str] = mapped_column(String(200), default="")
    venue_location: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    feed_last_modified: Mapped[str] = mapped_column(String(30), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_game_season_id_start_utc", "season_id", "start_utc"),
        Index("ix_game_status", "status"),
    )


class SkaterGameStats(Base):
    __tablename__ = "skater_game_stats"

    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("pro_team.id"))
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    shots: Mapped[int] = mapped_column(Integer, default=0)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    blocks: Mapped[int] = mapped_column(Integer, default=0)
    penalty_minutes: Mapped[int] = mapped_column(Integer, default=0)
    plus_minus: Mapped[int] = mapped_column(Integer, default=0)
    toi_seconds: Mapped[int] = mapped_column(Integer, default=0)
    faceoff_wins: Mapped[int] = mapped_column(Integer, default=0)
    faceoff_attempts: Mapped[int] = mapped_column(Integer, default=0)
    pp_goals: Mapped[int] = mapped_column(Integer, default=0)
    pp_assists: Mapped[int] = mapped_column(Integer, default=0)
    sh_goals: Mapped[int] = mapped_column(Integer, default=0)
    sh_assists: Mapped[int] = mapped_column(Integer, default=0)
    gwg: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_skater_game_stats_player_id", "player_id"),)


class GoalieGameStats(Base):
    __tablename__ = "goalie_game_stats"

    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("pro_team.id"))
    toi_seconds: Mapped[int] = mapped_column(Integer, default=0)
    shots_against: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str | None] = mapped_column(String(3))  # W / L / OTL / SOL
    shutout: Mapped[bool] = mapped_column(Boolean, default=False)
    started: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_goalie_game_stats_player_id", "player_id"),)


class RawSnapshot(Base):
    """Bronze layer: every distinct response body, so we can reprocess without refetching."""

    __tablename__ = "raw_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30), default="hockeytech")
    url: Mapped[str] = mapped_column(String(500))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict | list] = mapped_column(JSONB)

    __table_args__ = (Index("ix_raw_snapshot_url_fetched_at", "url", "fetched_at"),)
