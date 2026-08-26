"""record which sources saw each workout

Backfilled to the single source already on the row: that is exactly what we
know about workouts written before dedup started keeping the evidence, and
inventing agreement that was never observed would be worse than a short list.

Revision ID: f27b9e04c518
Revises: d93a4c1e7b62
Create Date: 2026-08-26 20:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f27b9e04c518"
down_revision: Union[str, None] = "d93a4c1e7b62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workouts", sa.Column("contributing_sources", JSONB(), nullable=True)
    )
    op.execute(
        "UPDATE workouts SET contributing_sources = jsonb_build_array(source) "
        "WHERE contributing_sources IS NULL"
    )


def downgrade() -> None:
    op.drop_column("workouts", "contributing_sources")
