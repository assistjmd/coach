"""count how many times a brief has been rewritten

Revision ID: b7d419e2c845
Revises: a4e1c86df093
Create Date: 2026-08-31 10:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d419e2c845"
down_revision: Union[str, None] = "a4e1c86df093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_briefs",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("daily_briefs", "revision")
