"""track when an account last used the app

Null for existing accounts rather than backfilled to now: claiming everyone was
active today would make the first week of retention numbers a fiction.

Revision ID: d93a4c1e7b62
Revises: b58c2d10fa73
Create Date: 2026-08-26 10:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d93a4c1e7b62"
down_revision: Union[str, None] = "b58c2d10fa73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_users_last_seen_at", table_name="users")
    op.drop_column("users", "last_seen_at")
