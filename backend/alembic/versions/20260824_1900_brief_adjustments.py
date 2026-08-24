"""record when the athlete changed today's session

Nullable and unbackfilled: an existing brief was not adjusted, and writing a
timestamp onto one would claim an edit that never happened.

Revision ID: c41d8e97b205
Revises: 9c2f1a7b4e30
Create Date: 2026-08-24 19:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c41d8e97b205"
down_revision: Union[str, None] = "9c2f1a7b4e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_briefs",
        sa.Column("adjusted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("daily_briefs", sa.Column("adjustment_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_briefs", "adjustment_note")
    op.drop_column("daily_briefs", "adjusted_at")
