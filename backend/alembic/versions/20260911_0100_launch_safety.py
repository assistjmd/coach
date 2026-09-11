"""Durable generation budget and signed subscription ordering.

Revision ID: a6f3912d804e
Revises: e91b47c2d508
"""
from alembic import op
import sqlalchemy as sa

revision = "a6f3912d804e"
down_revision = "e91b47c2d508"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generation_budgets",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("subscriptions", sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("transaction_id", sa.String(64), nullable=True))
    op.add_column("sleep_sessions", sa.Column("source_device", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("sleep_sessions", "source_device")
    op.drop_column("subscriptions", "transaction_id")
    op.drop_column("subscriptions", "signed_at")
    op.drop_table("generation_budgets")
