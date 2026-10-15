"""waitlist commissioner note

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "waitlist_signup",
        sa.Column("commissioner_note", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("waitlist_signup", "commissioner_note")
