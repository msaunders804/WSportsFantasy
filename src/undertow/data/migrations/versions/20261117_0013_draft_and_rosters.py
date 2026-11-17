"""draft, picks, queue and roster entries

New tables only; nothing existing is altered.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-17 14:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "draft",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "league_id",
            sa.Integer,
            sa.ForeignKey("league.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(10), nullable=False, server_default="live"),
        sa.Column("pick_seconds", sa.Integer, nullable=False),
        sa.Column("rounds", sa.Integer, nullable=False),
        sa.Column("order", JSONB, nullable=False),
        sa.Column("order_seed", sa.Integer, nullable=False),
        sa.Column("current_pick", sa.Integer, nullable=False, server_default="1"),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("paused_remaining", sa.Integer),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('live', 'paused', 'complete')", name="status_valid"),
        sa.CheckConstraint("pick_seconds >= 10 AND rounds >= 1", name="draft_sane"),
    )
    op.create_table(
        "draft_pick",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "draft_id", sa.Integer, sa.ForeignKey("draft.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("overall_no", sa.Integer, nullable=False),
        sa.Column("round", sa.Integer, nullable=False),
        sa.Column(
            "team_id",
            sa.Integer,
            sa.ForeignKey("fantasy_team.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("player.id"), nullable=False),
        sa.Column(
            "picked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("is_autopick", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("draft_id", "overall_no", name="uq_draft_pick_overall"),
        sa.UniqueConstraint("draft_id", "player_id", name="uq_draft_pick_player"),
    )
    op.create_index("ix_draft_pick_team_id", "draft_pick", ["team_id"])
    op.create_table(
        "draft_queue",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer,
            sa.ForeignKey("fantasy_team.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("player.id"), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.UniqueConstraint("team_id", "player_id", name="uq_draft_queue_team_player"),
    )
    op.create_table(
        "roster_entry",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "league_id",
            sa.Integer,
            sa.ForeignKey("league.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.Integer,
            sa.ForeignKey("fantasy_team.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("player.id"), nullable=False),
        sa.Column("acquired_via", sa.String(12), nullable=False, server_default="draft"),
        sa.Column(
            "acquired_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "acquired_via IN ('draft', 'free_agent', 'waiver', 'trade', 'commissioner')",
            name="acquired_via_valid",
        ),
    )
    op.create_index("ix_roster_entry_team_id", "roster_entry", ["team_id"])
    op.create_index(
        "uq_roster_entry_active_player",
        "roster_entry",
        ["league_id", "player_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("roster_entry")
    op.drop_table("draft_queue")
    op.drop_table("draft_pick")
    op.drop_table("draft")
