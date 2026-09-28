"""Auto-moderation: message_check, flagged_message, violation, chat_notice (§12).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_check",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("is_edit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("probabilities", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.chat_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_message_check_chat_created_at", "message_check", ["chat_id", "created_at"])

    op.create_table(
        "flagged_message",
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("entities", postgresql.JSONB(), nullable=True),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["check_id"], ["message_check.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("check_id"),
    )
    # §11 purge job: only rows still holding text are due.
    op.create_index(
        "ix_flagged_message_purge_at",
        "flagged_message",
        ["purge_at"],
        postgresql_where=sa.text("text IS NOT NULL"),
    )

    op.create_table(
        "violation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("restriction_seconds", sa.BigInteger(), nullable=True),
        sa.Column("restricted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("notice_dropped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["check_id"], ["message_check.id"]),
    )
    # §12: counting Active Violations.
    op.create_index(
        "ix_violation_active",
        "violation",
        ["chat_id", "user_id", "expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_violation_chat_created_at", "violation", ["chat_id", "created_at"])

    op.create_table(
        "chat_notice",
        sa.Column("violation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["violation_id"], ["violation.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("violation_id"),
    )
    # §11 removal job: not-yet-deleted notices only.
    op.create_index(
        "ix_chat_notice_delete_at",
        "chat_notice",
        ["delete_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_notice_delete_at", table_name="chat_notice")
    op.drop_table("chat_notice")
    op.drop_index("ix_violation_chat_created_at", table_name="violation")
    op.drop_index("ix_violation_active", table_name="violation")
    op.drop_table("violation")
    op.drop_index("ix_flagged_message_purge_at", table_name="flagged_message")
    op.drop_table("flagged_message")
    op.drop_index("ix_message_check_chat_created_at", table_name="message_check")
    op.drop_table("message_check")
