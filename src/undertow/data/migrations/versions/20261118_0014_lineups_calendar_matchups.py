"""fantasy calendar, lineup slots, per-game lineup locks and matchups

New tables only; nothing existing is altered.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-18 09:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

SLOTS = "('F', 'D', 'UTIL', 'G', 'BN', 'IR')"


def upgrade() -> None:
    op.create_table(
        "fantasy_week",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "season_id", sa.Integer, sa.ForeignKey("season.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("week_no", sa.Integer, nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("games", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("season_id", "week_no", name="uq_fantasy_week_season_no"),
        sa.CheckConstraint("week_no >= 1 AND end_date >= start_date", name="week_sane"),
    )
    op.create_table(
        "lineup_slot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer,
            sa.ForeignKey("fantasy_team.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("player.id"), nullable=False),
        sa.Column("slot", sa.String(4), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("team_id", "player_id", name="uq_lineup_slot_team_player"),
        sa.CheckConstraint(f"slot IN {SLOTS}", name="slot_valid"),
    )
    op.create_table(
        "lineup_lock",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer,
            sa.ForeignKey("fantasy_team.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("player.id"), nullable=False),
        sa.Column(
            "game_id", sa.Integer, sa.ForeignKey("game.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("slot", sa.String(4), nullable=False),
        sa.Column(
            "locked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "team_id", "player_id", "game_id", name="uq_lineup_lock_team_player_game"
        ),
        sa.CheckConstraint(f"slot IN {SLOTS}", name="slot_valid"),
    )
    op.create_index("ix_lineup_lock_game_id", "lineup_lock", ["game_id"])
    op.create_table(
        "matchup",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "league_id",
            sa.Integer,
            sa.ForeignKey("league.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_no", sa.Integer, nullable=False),
        sa.Column("last_week_no", sa.Integer, nullable=False),
        sa.Column(
            "home_team_id",
            sa.Integer,
            sa.ForeignKey("fantasy_team.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "away_team_id",
            sa.Integer,
            sa.ForeignKey("fantasy_team.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(10), nullable=False, server_default="scheduled"),
        sa.Column("home_points", sa.String(40)),
        sa.Column("away_points", sa.String(40)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("home_team_id <> away_team_id", name="two_teams"),
        sa.CheckConstraint("week_no >= 1 AND last_week_no >= week_no", name="weeks_sane"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'live', 'final', 'finalized')", name="status_valid"
        ),
        sa.UniqueConstraint("league_id", "week_no", "home_team_id", name="uq_matchup_week_home"),
        sa.UniqueConstraint("league_id", "week_no", "away_team_id", name="uq_matchup_week_away"),
    )
    op.create_index("ix_matchup_league_week", "matchup", ["league_id", "week_no"])


def downgrade() -> None:
    op.drop_table("matchup")
    op.drop_table("lineup_lock")
    op.drop_table("lineup_slot")
    op.drop_table("fantasy_week")
