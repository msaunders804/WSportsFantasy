"""league draft time

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15 12:40:59.115744
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("league", sa.Column("draft_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("league", "draft_at")
