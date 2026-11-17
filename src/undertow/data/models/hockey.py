"""Real-hockey tables. Primary keys are the HockeyTech IDs (stable integers)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from undertow.data import Base


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
    # When we first saw the game final, and how many follow-up re-fetches (+6 h, +24 h) are done.
    final_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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
    en_goals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

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


class ShotEvent(Base):
    """One shot attempt from play-by-play: on net (incl. goals), blocked, or penalty shot.

    The feed has no missed-net shots. Coordinates are the feed's raw values; in the games checked
    the home team always attacks toward low x (see docs/feed.md), but normalise in analysis code,
    not here. A goal is one row: the `shot` event merged with its `goal` event's flags.
    """

    __tablename__ = "shot_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"))
    kind: Mapped[str] = mapped_column(String(15))  # shot / blocked_shot / penalty_shot / goal
    feed_id: Mapped[int] = mapped_column(Integer)  # unique per (game, kind) only
    period: Mapped[int] = mapped_column(Integer)  # 1-3, 4 = overtime
    period_seconds: Mapped[int] = mapped_column(Integer)  # elapsed within the period
    team_id: Mapped[int] = mapped_column(ForeignKey("pro_team.id"))  # shooting team
    is_home: Mapped[bool] = mapped_column(Boolean)  # shooter is on the home team
    shooter_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    goalie_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"))  # null: empty net
    blocker_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"))
    x: Mapped[int | None] = mapped_column(Integer)
    y: Mapped[int | None] = mapped_column(Integer)
    shot_type: Mapped[str] = mapped_column(String(20), default="")
    quality: Mapped[int | None] = mapped_column(Integer)  # the feed's own quality code
    quality_label: Mapped[str] = mapped_column(String(40), default="")
    is_goal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_empty_net: Mapped[bool] = mapped_column(Boolean, default=False)
    is_power_play: Mapped[bool] = mapped_column(Boolean, default=False)
    is_short_handed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_penalty_shot: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("game_id", "kind", "feed_id"),
        Index("ix_shot_event_shooter_id", "shooter_id"),
    )


class PenaltyEvent(Base):
    """One penalty from play-by-play; with the clock it lets analysis rebuild strength state."""

    __tablename__ = "penalty_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("game.id"))
    feed_id: Mapped[int] = mapped_column(Integer)
    period: Mapped[int] = mapped_column(Integer)
    period_seconds: Mapped[int] = mapped_column(Integer)
    team_id: Mapped[int] = mapped_column(ForeignKey("pro_team.id"))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"))  # null: bench minor
    served_by_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"))
    minutes: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    penalty_class: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(String(100), default="")
    is_bench: Mapped[bool] = mapped_column(Boolean, default=False)
    is_penalty_shot: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("game_id", "feed_id"),)


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


class JobRun(Base):
    """One row per worker job execution; feeds the health page and failure alerts."""

    __tablename__ = "job_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ix_job_run_job_started_at", "job", "started_at"),)
