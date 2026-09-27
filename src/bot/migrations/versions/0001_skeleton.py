"""Walking skeleton: bot_user and fsm_state (ARCHITECTURE §12).

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_user",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("menu_message_id", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reachable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "fsm_state",
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("destiny", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("state", sa.String(length=256), nullable=True),
        sa.Column(
            "data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.PrimaryKeyConstraint("bot_id", "chat_id", "user_id", "thread_id", "destiny"),
    )


def downgrade() -> None:
    op.drop_table("fsm_state")
    op.drop_table("bot_user")
