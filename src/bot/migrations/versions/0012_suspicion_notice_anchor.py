"""Suspicion notice anchor: the Chat Notice of a later Punish lands right.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-01

A Suspicion may sit unanswered for hours before an Admin punishes it, and
the flagged message is usually deleted first — so the anchor the Chat
Notice needs (§7: a forum topic, or a comment thread's root under a
channel post) is frozen on the row when the Suspicion is raised.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suspicion", sa.Column("anchor_kind", sa.String(length=16), nullable=True))
    op.add_column("suspicion", sa.Column("anchor_message_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("suspicion", "anchor_message_id")
    op.drop_column("suspicion", "anchor_kind")
