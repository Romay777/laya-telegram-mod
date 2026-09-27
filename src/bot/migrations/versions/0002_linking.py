"""Linking: link_intent, chat, category, chat_category, admin_subscription.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.domain.linking import DEFAULT_EXPIRY_SECONDS, DEFAULT_LADDER
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUILTIN_CATEGORIES = ("spam", "ads", "insult")


def upgrade() -> None:
    op.create_table(
        "link_intent",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_link_intent_user_id", "link_intent", ["user_id"])
    op.create_index("ix_link_intent_expires_at", "link_intent", ["expires_at"])

    op.create_table(
        "chat",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="observation"),
        sa.Column("backend", sa.String(length=16), nullable=False, server_default="laya"),
        sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="balanced"),
        sa.Column("chat_language", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column(
            "ladder",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text(f"'{{{','.join(str(step) for step in DEFAULT_LADDER)}}}'"),
        ),
        sa.Column(
            "expiry_seconds",
            sa.BigInteger(),
            nullable=True,
            server_default=str(DEFAULT_EXPIRY_SECONDS),
        ),
        sa.Column("linker_id", sa.BigInteger(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_summary_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("chat_id"),
    )

    op.create_table(
        "category",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.execute(
        "INSERT INTO category (code, builtin) VALUES "
        + ", ".join(f"('{code}', true)" for code in BUILTIN_CATEGORIES)
        + " ON CONFLICT DO NOTHING"
    )

    op.create_table(
        "chat_category",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("category_code", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("violation_threshold", sa.Float(), nullable=True),
        sa.Column("suspicion_threshold", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_code"], ["category.code"]),
        sa.PrimaryKeyConstraint("chat_id", "category_code"),
    )

    op.create_table(
        "admin_subscription",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("alert_mode", sa.String(length=16), nullable=False, server_default="off"),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("admin_subscription")
    op.drop_table("chat_category")
    op.drop_table("category")
    op.drop_table("chat")
    op.drop_index("ix_link_intent_expires_at", table_name="link_intent")
    op.drop_index("ix_link_intent_user_id", table_name="link_intent")
    op.drop_table("link_intent")
