"""pending accounts and auth tokens

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15 09:21:15.527054
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('set_password')", name=op.f("ck_auth_token_purpose_valid")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name=op.f("fk_auth_token_user_id_user_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_token_token_hash")),
    )
    op.create_index("ix_auth_token_user_id", "auth_token", ["user_id"], unique=False)
    op.alter_column(
        "user_account", "password_hash", existing_type=sa.VARCHAR(length=255), nullable=True
    )


def downgrade() -> None:
    # Fails on purpose while any account still has no password: delete or finish those first.
    op.alter_column(
        "user_account", "password_hash", existing_type=sa.VARCHAR(length=255), nullable=False
    )
    op.drop_index("ix_auth_token_user_id", table_name="auth_token")
    op.drop_table("auth_token")
