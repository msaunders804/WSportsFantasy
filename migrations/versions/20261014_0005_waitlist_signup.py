"""waitlist signup

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waitlist_signup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("experience", sa.String(length=10), nullable=True),
        sa.Column("utm_source", sa.String(length=100), nullable=True),
        sa.Column("utm_medium", sa.String(length=100), nullable=True),
        sa.Column("utm_campaign", sa.String(length=100), nullable=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_waitlist_signup")),
        sa.UniqueConstraint("email", name=op.f("uq_waitlist_signup_email")),
    )


def downgrade() -> None:
    op.drop_table("waitlist_signup")
