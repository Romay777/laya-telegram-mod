"""Admin Alerts: admin_alert rows record every sent copy (§9, §12).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_alert",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.chat_id"], ondelete="CASCADE"),
    )
    # On a decision every copy is edited: find them all by their subject (§9).
    op.create_index("ix_admin_alert_subject", "admin_alert", ["subject_type", "subject_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_alert_subject", table_name="admin_alert")
    op.drop_table("admin_alert")
