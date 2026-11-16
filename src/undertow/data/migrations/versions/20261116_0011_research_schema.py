"""research schema: NCAA lines, draft picks, translation training

Private tables for third-party research data; the web app never reads them. New schema and
new tables only, nothing existing is altered.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-16 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

S = "research"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.create_table(
        "ncaa_game",
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.String(length=7), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("home_slug", sa.String(length=50), nullable=False),
        sa.Column("away_slug", sa.String(length=50), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("game_id", name=op.f("pk_ncaa_game")),
        schema=S,
    )
    op.create_table(
        "ncaa_player_game",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("team_slug", sa.String(length=50), nullable=False),
        sa.Column("first_name", sa.String(length=60), nullable=False),
        sa.Column("last_name", sa.String(length=60), nullable=False),
        sa.Column("position", sa.String(length=4), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("participated", sa.Boolean(), nullable=False),
        sa.Column("starter", sa.Boolean(), nullable=False),
        *[
            sa.Column(c, sa.Integer(), nullable=False)
            for c in (
                "shots",
                "goals",
                "assists",
                "blocks",
                "pp_goals",
                "sh_goals",
                "gw_goals",
                "minors",
                "majors",
                "faceoffs_won",
                "faceoffs_lost",
                "goalie_minutes",
                "goals_allowed",
                "saves",
            )
        ],
        sa.ForeignKeyConstraint(
            ["game_id"],
            [f"{S}.ncaa_game.game_id"],
            name=op.f("fk_ncaa_player_game_game_id_ncaa_game"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ncaa_player_game")),
        sa.UniqueConstraint(
            "game_id", "team_slug", "number", name=op.f("uq_ncaa_player_game_game_id")
        ),
        schema=S,
    )
    op.create_index(
        "ix_ncaa_player_game_name", "ncaa_player_game", ["team_slug", "last_name"], schema=S
    )
    op.create_table(
        "draft_pick",
        sa.Column("draft_year", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("pick", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("drafted_by", sa.String(length=60), nullable=False),
        sa.Column("player", sa.String(length=100), nullable=False),
        sa.Column("position", sa.String(length=6), nullable=False),
        sa.Column("school_or_club", sa.String(length=80), nullable=False),
        sa.Column("league", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("draft_year", "pick", name=op.f("pk_draft_pick")),
        schema=S,
    )
    ncaa_cols = []
    for tag in ("final", "prior"):
        ncaa_cols.append(sa.Column(f"{tag}_season", sa.String(length=7), nullable=False))
        ncaa_cols.append(sa.Column(f"{tag}_school_slug", sa.String(length=50), nullable=True))
        for c in ("gp", "goals", "assists", "shots", "saves", "goals_allowed", "goalie_minutes"):
            ncaa_cols.append(sa.Column(f"{tag}_{c}", sa.Integer(), nullable=True))
    op.create_table(
        "translation_training",
        sa.Column("draft_year", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("pwhl_player_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("player", sa.String(length=100), nullable=False),
        sa.Column("position", sa.String(length=6), nullable=False),
        sa.Column("kind", sa.String(length=1), nullable=False),
        sa.Column("draft_school", sa.String(length=80), nullable=False),
        sa.Column("pwhl_season_id", sa.Integer(), nullable=False),
        sa.Column("pwhl_gp", sa.Integer(), nullable=False),
        sa.Column("pwhl_goals", sa.Integer(), nullable=False),
        sa.Column("pwhl_assists", sa.Integer(), nullable=False),
        sa.Column("pwhl_shots", sa.Integer(), nullable=False),
        sa.Column("pwhl_toi_seconds", sa.Integer(), nullable=False),
        *ncaa_cols,
        sa.ForeignKeyConstraint(
            ["pwhl_player_id"],
            ["public.player.id"],
            name=op.f("fk_translation_training_pwhl_player_id_player"),
        ),
        sa.PrimaryKeyConstraint(
            "draft_year", "pwhl_player_id", name=op.f("pk_translation_training")
        ),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("translation_training", schema=S)
    op.drop_table("draft_pick", schema=S)
    op.drop_index("ix_ncaa_player_game_name", table_name="ncaa_player_game", schema=S)
    op.drop_table("ncaa_player_game", schema=S)
    op.drop_table("ncaa_game", schema=S)
    op.execute(f"DROP SCHEMA IF EXISTS {S}")
