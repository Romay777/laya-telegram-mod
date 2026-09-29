"""Backend incidents: backend_incident rows for the fallback router (§12, §5).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backend_incident",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The router asks one question per check: is this backend in trouble now?
    op.create_index("ix_backend_incident_backend", "backend_incident", ["backend"])


def downgrade() -> None:
    op.drop_index("ix_backend_incident_backend", table_name="backend_incident")
    op.drop_table("backend_incident")
