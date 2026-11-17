"""Draft and roster tables (Plan sections 3 and 4.6).

A league has at most one draft. Every pick is a row, and rosters are history (`released_at`),
not a mutable list. Constraints, not application checks alone, stop a double pick: one pick per
overall number, one pick per player per draft, and one active roster row per player per league.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from undertow.data import Base

DRAFT_STATUSES = ("live", "paused", "complete")


class Draft(Base):
    __tablename__ = "draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="live", server_default="live")
    pick_seconds: Mapped[int] = mapped_column(Integer)
    rounds: Mapped[int] = mapped_column(Integer)
    # fantasy_team ids in draft order (slot 1 first). Snake: even rounds run it backwards.
    order: Mapped[list] = mapped_column(JSONB)
    # Seed used to shuffle the order, kept so the draw can be reproduced and audited.
    order_seed: Mapped[int] = mapped_column(Integer)
    # 1-based number of the next pick. One past the last pick once the draft is complete.
    current_pick: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # When the team on the clock runs out of time (UTC). None while paused or complete.
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Seconds that were left on the clock when a commissioner paused.
    paused_remaining: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('live', 'paused', 'complete')", name="status_valid"),
        CheckConstraint("pick_seconds >= 10 AND rounds >= 1", name="draft_sane"),
    )


class DraftSelection(Base):
    """One pick in a draft."""

    __tablename__ = "draft_pick"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("draft.id", ondelete="CASCADE"))
    overall_no: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    team_id: Mapped[int] = mapped_column(ForeignKey("fantasy_team.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    picked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_autopick: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    __table_args__ = (
        UniqueConstraint("draft_id", "overall_no", name="uq_draft_pick_overall"),
        UniqueConstraint("draft_id", "player_id", name="uq_draft_pick_player"),
        Index("ix_draft_pick_team_id", "team_id"),
    )


class DraftQueueEntry(Base):
    """A player a team wants, in its own order. Autopick takes the first one still available."""

    __tablename__ = "draft_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("fantasy_team.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    rank: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("team_id", "player_id", name="uq_draft_queue_team_player"),)


class RosterEntry(Base):
    """A player on a fantasy team. History, not a list: a dropped player gets `released_at`."""

    __tablename__ = "roster_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(ForeignKey("fantasy_team.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    acquired_via: Mapped[str] = mapped_column(String(12), default="draft")
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "acquired_via IN ('draft', 'free_agent', 'waiver', 'trade', 'commissioner')",
            name="acquired_via_valid",
        ),
        # A player can be on only one team in a league at a time.
        Index(
            "uq_roster_entry_active_player",
            "league_id",
            "player_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index("ix_roster_entry_team_id", "team_id"),
    )
