"""Add remittances.quarantine_reason -- Phase 5 ingestion needs somewhere to
store a human-readable diagnostic when a file is quarantined (unparseable,
undecodable, or virus-flagged) so "quarantined with a useful message" is
actually queryable, not just implied by status="quarantined".

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("remittances", sa.Column("quarantine_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("remittances", "quarantine_reason")
