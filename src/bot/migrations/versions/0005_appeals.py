"""Appeals: one appeal row per violation, unique on the Violation (§8, §12).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appeal",
        sa.Column("id", sa.Integer(), primary_key=True),
        # One Appeal per Violation (§8): the UNIQUE constraint refuses a second.
        sa.Column("violation_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["violation_id"], ["violation.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("appeal")
