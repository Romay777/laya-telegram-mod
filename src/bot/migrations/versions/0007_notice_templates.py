"""Notice Templates: one Admin-defined template per chat (§12, §14).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notice_template",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        # The Admin's message as received: text with formatting (§14).
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("entities", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("notice_template")
