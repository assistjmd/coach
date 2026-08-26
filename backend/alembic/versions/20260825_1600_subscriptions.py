"""subscriptions and the free trial

trial_ends_at is left null for existing accounts and filled in on first sight by
`entitlement.ensure_trial`, so current testers get a full trial from now rather
than one that expired before it existed.

Revision ID: b58c2d10fa73
Revises: e7a3f2b91c48
Create Date: 2026-08-25 16:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "b58c2d10fa73"
down_revision: Union[str, None] = "e7a3f2b91c48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_transaction_id", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False, server_default="Production"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("original_transaction_id", name="uq_subscription_original_tx"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_expires_at", "subscriptions", ["expires_at"])
    op.create_index(
        "ix_subscriptions_original_transaction_id",
        "subscriptions",
        ["original_transaction_id"],
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_column("users", "trial_ends_at")
