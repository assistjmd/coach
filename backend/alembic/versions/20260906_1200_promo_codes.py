"""promo codes and their redemptions

Revision ID: e91b47c2d508
Revises: c8e520f3a961
Create Date: 2026-09-06 12:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "e91b47c2d508"
down_revision: Union[str, None] = "c8e520f3a961"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promo_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("extra_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"])
    op.create_table(
        "promo_redemptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("promo_code_id", UUID(as_uuid=True),
                  sa.ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("extended_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "promo_code_id", name="uq_promo_once_per_user"),
    )
    op.create_index("ix_promo_redemptions_user_id", "promo_redemptions", ["user_id"])
    op.create_index("ix_promo_redemptions_promo_code_id", "promo_redemptions", ["promo_code_id"])


def downgrade() -> None:
    op.drop_table("promo_redemptions")
    op.drop_table("promo_codes")
