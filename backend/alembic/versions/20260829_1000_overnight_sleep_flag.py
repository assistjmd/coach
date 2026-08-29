"""record whether a brief had last night's sleep

Existing rows default to true rather than false: they are historical and must
not each trigger a regeneration the first time this deploys.

Revision ID: a4e1c86df093
Revises: f27b9e04c518
Create Date: 2026-08-29 10:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4e1c86df093"
down_revision: Union[str, None] = "f27b9e04c518"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_briefs",
        sa.Column(
            "had_overnight_sleep", sa.Boolean(), nullable=False, server_default="true"
        ),
    )
    # New rows decide for themselves; only the backfill is optimistic.
    op.alter_column("daily_briefs", "had_overnight_sleep", server_default="false")


def downgrade() -> None:
    op.drop_column("daily_briefs", "had_overnight_sleep")
