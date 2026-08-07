"""user date of birth for the 16+ age gate

The terms require users to be 16 or older and nothing asked. Stored as a date
rather than a boolean so the record is the answer they gave, not the fact that
they tapped a button.

Nullable, and left null for existing accounts. Backfilling a guess would put
fabricated personal data in the table, and defaulting everyone to "old enough"
would make the column worthless as evidence.

Revision ID: 9c2f1a7b4e30
Revises: 680baabdd63b
Create Date: 2026-08-07 15:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c2f1a7b4e30"
down_revision: Union[str, None] = "680baabdd63b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("date_of_birth", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "date_of_birth")
