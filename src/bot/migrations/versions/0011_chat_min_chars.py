"""Per-chat minimum length: the short-message filter moves into `chat`.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30

The §4 step 4 skip threshold leaves the instance-wide config for each
chat's own `min_chars` (§12, §13): link-free messages shorter than it are
skipped as `skipped_short`. Existing chats keep their behavior at the new
`DEFAULT_MIN_CHARS` default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.domain.linking import DEFAULT_MIN_CHARS

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat",
        sa.Column("min_chars", sa.Integer(), nullable=False, server_default=str(DEFAULT_MIN_CHARS)),
    )


def downgrade() -> None:
    op.drop_column("chat", "min_chars")
