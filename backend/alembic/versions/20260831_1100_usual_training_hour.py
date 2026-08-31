"""remember when the athlete usually trains

Revision ID: c8e520f3a961
Revises: b7d419e2c845
Create Date: 2026-08-31 11:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8e520f3a961"
down_revision: Union[str, None] = "b7d419e2c845"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("usual_training_hour", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "usual_training_hour")
