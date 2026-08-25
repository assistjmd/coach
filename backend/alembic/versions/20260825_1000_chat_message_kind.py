"""separate the chat and session-adjustment allowances

Existing rows default to "chat", which is what they were — the adjust endpoint
did not exist when they were written.

Revision ID: e7a3f2b91c48
Revises: c41d8e97b205
Create Date: 2026-08-25 10:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3f2b91c48"
down_revision: Union[str, None] = "c41d8e97b205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "kind", sa.String(16), nullable=False, server_default="chat"
        ),
    )
    op.create_index(
        "ix_chat_messages_user_kind_created",
        "chat_messages",
        ["user_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_user_kind_created", table_name="chat_messages")
    op.drop_column("chat_messages", "kind")
