"""fantasy accounts leagues teams

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15 08:34:19.276025
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=60), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_account")),
        sa.UniqueConstraint("email", name=op.f("uq_user_account_email")),
    )
    op.create_table(
        "auth_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name=op.f("fk_auth_session_user_id_user_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_session_token_hash")),
    )
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"], unique=False)
    op.create_table(
        "league",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="setup", nullable=False),
        sa.Column("invite_code", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('setup', 'drafting', 'in_season', 'playoffs', 'complete')",
            name=op.f("ck_league_status_valid"),
        ),
        sa.CheckConstraint(
            "size >= 4 AND size <= 12 AND MOD(size, 2) = 0",
            name=op.f("ck_league_size_even_4_to_12"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_league_created_by_id_user_account"),
        ),
        sa.ForeignKeyConstraint(
            ["season_id"], ["season.id"], name=op.f("fk_league_season_id_season")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_league")),
        sa.UniqueConstraint("invite_code", name=op.f("uq_league_invite_code")),
        sa.UniqueConstraint("season_id", "name", name="uq_league_season_name"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["user_account.id"],
            name=op.f("fk_audit_log_actor_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["league.id"],
            name=op.f("fk_audit_log_league_id_league"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_league_id", "audit_log", ["league_id"], unique=False)
    op.create_table(
        "fantasy_team",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("waiver_priority", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["league.id"],
            name=op.f("fk_fantasy_team_league_id_league"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user_account.id"],
            name=op.f("fk_fantasy_team_owner_id_user_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fantasy_team")),
        sa.UniqueConstraint("league_id", "owner_id", name="uq_fantasy_team_league_owner"),
    )
    op.create_index(
        "uq_fantasy_team_league_name",
        "fantasy_team",
        ["league_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "league_member",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="member", nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('member', 'commissioner', 'co_commissioner')",
            name=op.f("ck_league_member_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["league.id"],
            name=op.f("fk_league_member_league_id_league"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name=op.f("fk_league_member_user_id_user_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_league_member")),
        sa.UniqueConstraint("league_id", "user_id", name="uq_league_member_league_user"),
    )
    op.create_index("ix_league_member_user_id", "league_member", ["user_id"], unique=False)
    op.create_index(
        "uq_league_member_one_co_commissioner",
        "league_member",
        ["league_id"],
        unique=True,
        postgresql_where=sa.text("role = 'co_commissioner'"),
    )
    op.create_index(
        "uq_league_member_one_commissioner",
        "league_member",
        ["league_id"],
        unique=True,
        postgresql_where=sa.text("role = 'commissioner'"),
    )
    op.create_table(
        "league_settings",
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("roster_f", sa.Integer(), nullable=False),
        sa.Column("roster_d", sa.Integer(), nullable=False),
        sa.Column("roster_util", sa.Integer(), nullable=False),
        sa.Column("roster_g", sa.Integer(), nullable=False),
        sa.Column("roster_bench", sa.Integer(), nullable=False),
        sa.Column("roster_ir", sa.Integer(), nullable=False),
        sa.Column("max_goalies", sa.Integer(), nullable=False),
        sa.Column("scoring_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("waiver_hours", sa.Integer(), nullable=False),
        sa.Column("trade_review_hours", sa.Integer(), nullable=False),
        sa.Column("trade_deadline_week", sa.Integer(), nullable=False),
        sa.Column("regular_season_weeks", sa.Integer(), nullable=False),
        sa.Column("playoff_teams", sa.Integer(), nullable=False),
        sa.Column("lock_rule", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "roster_f >= 0 AND roster_d >= 0 AND roster_util >= 0 AND roster_g >= 1 AND roster_bench >= 0 AND roster_ir >= 0 AND max_goalies >= roster_g",
            name=op.f("ck_league_settings_roster_sane"),
        ),
        sa.CheckConstraint(
            "waiver_hours >= 0 AND trade_review_hours >= 0 AND trade_deadline_week >= 1 AND regular_season_weeks >= 1 AND playoff_teams >= 2",
            name=op.f("ck_league_settings_timing_sane"),
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["league.id"],
            name=op.f("fk_league_settings_league_id_league"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("league_id", name=op.f("pk_league_settings")),
    )


def downgrade() -> None:
    op.drop_table("league_settings")
    op.drop_index(
        "uq_league_member_one_commissioner",
        table_name="league_member",
        postgresql_where=sa.text("role = 'commissioner'"),
    )
    op.drop_index(
        "uq_league_member_one_co_commissioner",
        table_name="league_member",
        postgresql_where=sa.text("role = 'co_commissioner'"),
    )
    op.drop_index("ix_league_member_user_id", table_name="league_member")
    op.drop_table("league_member")
    op.drop_index("uq_fantasy_team_league_name", table_name="fantasy_team")
    op.drop_table("fantasy_team")
    op.drop_index("ix_audit_log_league_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("league")
    op.drop_index("ix_auth_session_user_id", table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_table("user_account")
