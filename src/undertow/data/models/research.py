"""Private research tables in the `research` schema. The web app never reads these.

Holds third-party NCAA data as clean rows (not raw JSON) plus the translation training table.
Raw responses stay in local caches. Nothing here is published; only derived ratings are.
"""

from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from undertow.data import Base

SCHEMA = "research"


class NcaaGame(Base):
    __tablename__ = "ncaa_game"
    __table_args__ = {"schema": SCHEMA}

    game_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season: Mapped[str] = mapped_column(String(7))  # '2023-24'
    game_date: Mapped[date] = mapped_column(Date)
    home_slug: Mapped[str] = mapped_column(String(50))
    away_slug: Mapped[str] = mapped_column(String(50))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)


class NcaaPlayerGame(Base):
    """One player's line in one NCAA box score. Names are as the source gives them (some games
    abbreviate a first name to an initial); there is no player id in the source."""

    __tablename__ = "ncaa_player_game"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.ncaa_game.game_id"))
    team_slug: Mapped[str] = mapped_column(String(50))
    first_name: Mapped[str] = mapped_column(String(60))
    last_name: Mapped[str] = mapped_column(String(60))
    position: Mapped[str] = mapped_column(String(4))
    number: Mapped[int | None] = mapped_column(Integer)
    participated: Mapped[bool] = mapped_column(Boolean)
    starter: Mapped[bool] = mapped_column(Boolean)
    shots: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    blocks: Mapped[int] = mapped_column(Integer, default=0)
    pp_goals: Mapped[int] = mapped_column(Integer, default=0)
    sh_goals: Mapped[int] = mapped_column(Integer, default=0)
    gw_goals: Mapped[int] = mapped_column(Integer, default=0)
    minors: Mapped[int] = mapped_column(Integer, default=0)
    majors: Mapped[int] = mapped_column(Integer, default=0)
    faceoffs_won: Mapped[int] = mapped_column(Integer, default=0)
    faceoffs_lost: Mapped[int] = mapped_column(Integer, default=0)
    goalie_minutes: Mapped[int] = mapped_column(Integer, default=0)
    goals_allowed: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("game_id", "team_slug", "number"),
        Index("ix_ncaa_player_game_name", "team_slug", "last_name"),
        {"schema": SCHEMA},
    )


class DraftPick(Base):
    __tablename__ = "draft_pick"
    __table_args__ = {"schema": SCHEMA}

    draft_year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    pick: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    round: Mapped[int] = mapped_column(Integer)
    drafted_by: Mapped[str] = mapped_column(String(60))
    player: Mapped[str] = mapped_column(String(100))
    position: Mapped[str] = mapped_column(String(6))
    school_or_club: Mapped[str] = mapped_column(String(80))
    league: Mapped[str] = mapped_column(String(20))  # NCAA / USports / Europe / PWHL / not stated


class TranslationTraining(Base):
    """One row per 2024/2025 draftee who played NCAA and then the PWHL: NCAA lines for the last
    two seasons before the draft (at the draft school, or the school found if she transferred)
    next to her first PWHL season. Source of the NCAA-to-PWHL translation fit."""

    __tablename__ = "translation_training"
    __table_args__ = {"schema": SCHEMA}

    draft_year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    pwhl_player_id: Mapped[int] = mapped_column(
        ForeignKey("player.id"), primary_key=True, autoincrement=False
    )
    player: Mapped[str] = mapped_column(String(100))
    position: Mapped[str] = mapped_column(String(6))
    kind: Mapped[str] = mapped_column(String(1))  # S skater, G goalie
    draft_school: Mapped[str] = mapped_column(String(80))
    pwhl_season_id: Mapped[int] = mapped_column(Integer)
    pwhl_gp: Mapped[int] = mapped_column(Integer)
    pwhl_goals: Mapped[int] = mapped_column(Integer)
    pwhl_assists: Mapped[int] = mapped_column(Integer)
    pwhl_shots: Mapped[int] = mapped_column(Integer)
    pwhl_toi_seconds: Mapped[int] = mapped_column(Integer)
    final_season: Mapped[str] = mapped_column(String(7))
    final_school_slug: Mapped[str | None] = mapped_column(String(50))
    final_gp: Mapped[int | None] = mapped_column(Integer)
    final_goals: Mapped[int | None] = mapped_column(Integer)
    final_assists: Mapped[int | None] = mapped_column(Integer)
    final_shots: Mapped[int | None] = mapped_column(Integer)
    final_saves: Mapped[int | None] = mapped_column(Integer)
    final_goals_allowed: Mapped[int | None] = mapped_column(Integer)
    final_goalie_minutes: Mapped[int | None] = mapped_column(Integer)
    prior_season: Mapped[str] = mapped_column(String(7))
    prior_school_slug: Mapped[str | None] = mapped_column(String(50))
    prior_gp: Mapped[int | None] = mapped_column(Integer)
    prior_goals: Mapped[int | None] = mapped_column(Integer)
    prior_assists: Mapped[int | None] = mapped_column(Integer)
    prior_shots: Mapped[int | None] = mapped_column(Integer)
    prior_saves: Mapped[int | None] = mapped_column(Integer)
    prior_goals_allowed: Mapped[int | None] = mapped_column(Integer)
    prior_goalie_minutes: Mapped[int | None] = mapped_column(Integer)
