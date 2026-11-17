"""private leagues: league.kind and emailed league invites

Existing leagues are the Undertow-run ones, so they become "house". Nothing existing is dropped.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17 09:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "league", sa.Column("kind", sa.String(10), nullable=False, server_default="house")
    )
    op.create_check_constraint("kind_valid", "league", "kind IN ('house', 'private')")
    op.create_table(
        "league_invite",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "league_id", sa.Integer, sa.ForeignKey("league.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column(
            "invited_by_id", sa.Integer, sa.ForeignKey("user_account.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("league_id", "email", name="uq_league_invite_league_email"),
    )


def downgrade() -> None:
    op.drop_table("league_invite")
    op.drop_constraint("kind_valid", "league", type_="check")
    op.drop_column("league", "kind")
