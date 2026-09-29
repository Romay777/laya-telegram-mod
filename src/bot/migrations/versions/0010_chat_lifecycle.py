"""Chat lifecycle: the purge needs a full cascade and its §11 index.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29

Deleting a Removed Chat (§11) drops every chat-scoped row by cascade
(ADR-0001). `violation.check_id` still pointed at `message_check.id`
without one, so a purge that cascaded the checks would fail on it; the
constraint is rebuilt with `ondelete="CASCADE"`. A partial index over
`chat.removed_at` gives the scheduler's due-purge query its §12 index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("violation_check_id_fkey", "violation", type_="foreignkey")
    op.create_foreign_key(
        "violation_check_id_fkey",
        "violation",
        "message_check",
        ["check_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_chat_removed_at",
        "chat",
        ["removed_at"],
        postgresql_where=sa.text("status = 'removed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_removed_at", table_name="chat")
    op.drop_constraint("violation_check_id_fkey", "violation", type_="foreignkey")
    op.create_foreign_key(
        "violation_check_id_fkey", "violation", "message_check", ["check_id"], ["id"]
    )
